import json

import pytest

from dunnit.contract import parse_contract
from dunnit.onboarding import detect, render_contract


def test_python_detection_requires_test_evidence(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_app():\n    assert True\n")
    assert detect(tmp_path, "python").checks == []

    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    detection = detect(tmp_path, "python")
    assert detection.checks[0].argv == ["python", "-m", "pytest", "-q"]
    assert "pytest.ini" in detection.protected
    assert "**/test_*.py" in detection.test_globs


def test_rendered_contract_is_explicit_and_runtime_valid(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    body = render_contract(detect(tmp_path, "python"), base="origin/main")
    contract = parse_contract(body, source="generated")

    assert contract.version == 2
    assert contract.base == "origin/main"
    assert contract.checks[0].dir == "."
    assert contract.checks[0].timeout == 600
    assert contract.checks[0].env == {"CI": "1"}
    assert contract.checks[0].writes == []


def test_render_never_emits_empty_contract(tmp_path):
    with pytest.raises(ValueError, match="no proof commands"):
        render_contract(detect(tmp_path))


def test_node_workspace_materializes_per_package_directories(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
    package = tmp_path / "packages" / "web app"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest", "lint": "eslint ."}})
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")

    detection = detect(tmp_path, "node")

    assert [item.directory for item in detection.checks] == [
        "packages/web app",
        "packages/web app",
    ]
    assert all(item.argv[0] == "pnpm" for item in detection.checks)
    assert "packages/web app/**/*.test.*" in detection.test_globs
    assert "packages/web app/package.json" in detection.protected
    assert "package.json" in detection.protected
    assert "pnpm-lock.yaml" in detection.protected
    assert len({item.name for item in detection.checks}) == 2


def test_pnpm_workspace_manifest_is_declared_evidence(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - apps/*\n")
    package = tmp_path / "apps" / "api"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"scripts": {"test": "node test.js"}}))

    detection = detect(tmp_path, "node")

    assert len(detection.checks) == 1
    assert detection.checks[0].directory == "apps/api"
    assert {"package.json", "pnpm-workspace.yaml", "apps/api/package.json"} <= set(
        detection.protected
    )


def test_mixed_preset_uses_declared_ecosystems(tmp_path):
    (tmp_path / "go.mod").write_text("module example.test/demo\n")
    (tmp_path / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "node test.js"}}))

    detection = detect(tmp_path, "mixed")
    names = {item.name for item in detection.checks}

    assert {"node-tests", "go-tests", "go-vet", "rust-tests"} <= names
    assert "**/*_test.go" in detection.test_globs
    assert "go.mod" in detection.protected


def test_workspace_escape_pattern_is_ignored(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["../*"]}))
    detection = detect(tmp_path, "node")
    assert detection.checks == []
    assert any("repository root" in issue for issue in detection.issues)


def test_overlapping_workspace_patterns_are_deduplicated(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"workspaces": ["packages/*", "packages/api"]})
    )
    package = tmp_path / "packages" / "api"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    detection = detect(tmp_path, "node")
    assert len(detection.checks) == 1


def test_multiple_node_lockfiles_are_reported_as_ambiguous(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (tmp_path / "yarn.lock").write_text("# yarn\n")
    detection = detect(tmp_path, "node")
    assert detection.issues
    with pytest.raises(ValueError, match="multiple Node lockfiles"):
        render_contract(detection)


def test_declared_node_package_manager_is_materialized_without_a_lockfile(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "pnpm@10.2.0",
                "scripts": {"test": "vitest"},
            }
        )
    )

    detection = detect(tmp_path, "node")

    assert detection.issues == []
    assert detection.checks[0].argv == ["pnpm", "run", "test"]
    assert "package.json" in detection.protected


def test_declared_node_manager_conflicting_with_lockfile_is_ambiguous(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"packageManager": "pnpm@10", "scripts": {"test": "vitest"}})
    )
    (tmp_path / "yarn.lock").write_text("# yarn\n")

    detection = detect(tmp_path, "node")

    assert detection.issues
    with pytest.raises(ValueError, match="packageManager signals"):
        render_contract(detection)


def test_go_workspace_materializes_each_declared_module_directory(tmp_path):
    api = tmp_path / "services" / "api"
    worker = tmp_path / "services" / "background worker"
    api.mkdir(parents=True)
    worker.mkdir(parents=True)
    (api / "go.mod").write_text("module example.test/api\n")
    (worker / "go.mod").write_text("module example.test/worker\n")
    (tmp_path / "go.work").write_text(
        'go 1.24\nuse (\n  ./services/api\n  "./services/background worker"\n)\n'
    )

    detection = detect(tmp_path, "go")

    assert detection.issues == []
    assert [item.directory for item in detection.checks] == [
        "services/api",
        "services/api",
        "services/background worker",
        "services/background worker",
    ]
    assert {item.name for item in detection.checks} == {
        "go-tests-services-api",
        "go-vet-services-api",
        "go-tests-services-background-worker",
        "go-vet-services-background-worker",
    }
    assert "services/api/**/*_test.go" in detection.test_globs
    assert "services/background worker/**/*_test.go" in detection.test_globs
    assert "go.work" in detection.protected
    assert "services/api/go.mod" in detection.protected
    assert "services/background worker/go.mod" in detection.protected
    assert parse_contract(render_contract(detection)).version == 2


def test_invalid_go_workspace_paths_block_generation(tmp_path):
    (tmp_path / "go.work").write_text("go 1.24\nuse ../outside\n")

    detection = detect(tmp_path, "go")

    assert any("escapes the repository" in issue for issue in detection.issues)
    with pytest.raises(ValueError, match="escapes the repository"):
        render_contract(detection)


def test_colliding_workspace_slugs_block_invalid_contract_generation(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"workspaces": ["packages/*"]})
    )
    for name in ("web-app", "web_app"):
        package = tmp_path / "packages" / name
        package.mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))

    detection = detect(tmp_path, "node")

    assert any("duplicate check names" in issue for issue in detection.issues)
    with pytest.raises(ValueError, match="duplicate check names"):
        render_contract(detection)


def test_default_npm_placeholder_is_not_proof(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}
        )
    )
    assert detect(tmp_path, "node").checks == []


@pytest.mark.parametrize("pattern", ["C:/outside/*", "//server/share/*", "packages/../../*"])
def test_workspace_patterns_are_validated_for_every_supported_os(tmp_path, pattern):
    (tmp_path / "package.json").write_text(
        json.dumps({"workspaces": [pattern], "scripts": {"test": "vitest"}})
    )
    detection = detect(tmp_path, "node")
    assert any("invalid workspace pattern" in issue for issue in detection.issues)
    with pytest.raises(ValueError, match="workspace pattern"):
        render_contract(detection)


def test_python_manifest_mentions_and_comments_do_not_infer_tools(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndescription = "pytest"\n# [tool.pytest.ini_options]\n'
        '# [tool.ruff]\n[project.urls]\npytest = "https://pytest.org"\n'
    )
    assert detect(tmp_path, "python").checks == []

    (tmp_path / "requirements-dev.txt").write_text("pytest>=8\n")
    detection = detect(tmp_path, "python")
    assert [check.name for check in detection.checks] == ["tests"]
    assert "requirements-dev.txt" in detection.protected


def test_clear_noop_node_scripts_are_not_generated_as_proof(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "echo tests passed", "lint": "true"}})
    )
    assert detect(tmp_path, "node").checks == []


def test_rust_workspace_member_integration_tests_are_in_tamper_scope(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[workspace]\nmembers = ['crates/api']\n")
    detection = detect(tmp_path, "rust")
    assert "**/tests/**" in detection.test_globs
    assert "**/src/**" not in detection.test_globs


def test_unknown_preset_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown preset: java"):
        detect(tmp_path, "java")


@pytest.mark.parametrize(
    ("filename", "contents", "expected_names", "protected"),
    [
        ("conftest.py", "# pytest hooks\n", ["tests"], "conftest.py"),
        (
            "pyproject.toml",
            "[tool.pytest.ini_options]\naddopts = '-q'\n[tool.ruff]\nline-length = 100\n",
            ["tests", "lint"],
            "pyproject.toml",
        ),
        (
            "setup.cfg",
            "[tool:pytest]\naddopts = -q\n[ruff]\nline-length = 100\n",
            ["tests", "lint"],
            "setup.cfg",
        ),
    ],
)
def test_python_tool_configuration_is_materialized(
    tmp_path, filename, contents, expected_names, protected
):
    (tmp_path / filename).write_text(contents)

    detection = detect(tmp_path, "python")

    assert [check.name for check in detection.checks] == expected_names
    assert protected in detection.protected


@pytest.mark.parametrize(
    "pyproject",
    [
        "[tool.poetry.group.test.dependencies]\npytest = '^8'\n",
        "[project]\ndependencies = ['pytest>=8']\n",
        "[dependency-groups]\ntest = [\n  'coverage>=7',\n  'pytest>=8',\n]\n",
        "[tool.uv]\ndev-dependencies = [\n  'pytest-xdist>=3',\n]\n",
        "[tool.hatch.envs.test]\ndependencies = [\n  'pytest>=8',\n]\n",
    ],
)
def test_python_dependency_formats_are_explicit_pytest_evidence(tmp_path, pyproject):
    (tmp_path / "pyproject.toml").write_text(pyproject)

    detection = detect(tmp_path, "python")
    assert [check.name for check in detection.checks] == ["tests"]
    assert "pyproject.toml" in detection.protected


def test_dedicated_test_configuration_is_protected_even_without_a_command(tmp_path):
    (tmp_path / "jest.config.js").write_text("export default {}\n")

    detection = detect(tmp_path, "node")

    assert detection.checks == []
    assert "jest.config.js" in detection.protected


@pytest.mark.parametrize(
    ("package_manager", "lockfile", "expected_argv"),
    [
        (None, None, ["npm", "run", "test", "--silent"]),
        ("yarn@4.6.0", None, ["yarn", "run", "test"]),
        ("bun@1.2.0", None, ["bun", "run", "test"]),
        (None, "package-lock.json", ["npm", "run", "test", "--silent"]),
        (None, "bun.lockb", ["bun", "run", "test"]),
    ],
)
def test_node_package_manager_signals_select_portable_argv(
    tmp_path, package_manager, lockfile, expected_argv
):
    manifest = {"scripts": {"test": "vitest"}}
    if package_manager is not None:
        manifest["packageManager"] = package_manager
    (tmp_path / "package.json").write_text(json.dumps(manifest))
    if lockfile is not None:
        (tmp_path / lockfile).write_text("")

    detection = detect(tmp_path, "node")

    assert detection.issues == []
    assert detection.checks[0].argv == expected_argv
    if lockfile is not None:
        assert lockfile in detection.protected


@pytest.mark.parametrize(
    ("package_manager", "message"),
    [
        ("", "non-empty string"),
        (42, "non-empty string"),
        ("deno@2", "unsupported declared packageManager"),
    ],
)
def test_invalid_node_package_manager_blocks_generation(tmp_path, package_manager, message):
    (tmp_path / "package.json").write_text(
        json.dumps({"packageManager": package_manager, "scripts": {"test": "vitest"}})
    )

    detection = detect(tmp_path, "node")

    assert any(message in issue for issue in detection.issues)
    with pytest.raises(ValueError, match=message):
        render_contract(detection)


@pytest.mark.parametrize(
    "manifest",
    [
        "[]",
        "{not json",
        json.dumps({"scripts": ["vitest"]}),
    ],
)
def test_malformed_or_unsupported_node_manifest_does_not_invent_checks(tmp_path, manifest):
    (tmp_path / "package.json").write_text(manifest)

    assert detect(tmp_path, "node").checks == []


def test_workspace_object_and_exclusion_patterns_are_honored(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"workspaces": {"packages": ["packages/*", "!packages/private"]}})
    )
    for name in ("public", "private"):
        package = tmp_path / "packages" / name
        package.mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))

    detection = detect(tmp_path, "node")

    assert detection.issues == []
    assert [check.directory for check in detection.checks] == ["packages/public"]


@pytest.mark.parametrize("manifest", ["{not json", "[]"])
def test_malformed_declared_workspace_package_blocks_generation(tmp_path, manifest):
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
    package = tmp_path / "packages" / "broken"
    package.mkdir(parents=True)
    (package / "package.json").write_text(manifest)

    detection = detect(tmp_path, "node")

    assert any("package.json is not a JSON object" in issue for issue in detection.issues)
    with pytest.raises(ValueError, match="package.json is not a JSON object"):
        render_contract(detection)


def test_declared_workspace_pattern_without_packages_is_actionable(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))

    detection = detect(tmp_path, "node")

    assert detection.issues == ["workspace pattern 'packages/*' matched no paths"]


@pytest.mark.parametrize(
    ("workspaces", "message"),
    [
        ("packages/*", "must be a list"),
        (["packages/*", 7], "patterns must be strings"),
        ([""], "pattern must not be empty"),
    ],
)
def test_malformed_package_workspace_declarations_are_actionable(
    tmp_path, workspaces, message
):
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": workspaces}))

    detection = detect(tmp_path, "node")

    assert any(message in issue for issue in detection.issues)
    with pytest.raises(ValueError, match=message):
        render_contract(detection)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("packages: [unterminated\n", "could not parse pnpm-workspace.yaml"),
        ("catalog: {}\n", "must contain a packages list"),
        ("packages:\n  - apps/*\n  - 7\n", "patterns must be strings"),
    ],
)
def test_malformed_pnpm_workspace_is_actionable(tmp_path, contents, message):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pnpm-workspace.yaml").write_text(contents)

    detection = detect(tmp_path, "node")

    assert any(message in issue for issue in detection.issues)
    with pytest.raises(ValueError, match=message):
        render_contract(detection)


def test_empty_go_workspace_is_not_treated_as_a_proof_harness(tmp_path):
    (tmp_path / "go.work").write_text("go 1.24\n")

    detection = detect(tmp_path, "go")

    assert detection.checks == []
    assert detection.issues == ["go.work declares no usable module paths"]
    with pytest.raises(ValueError, match="no usable module paths"):
        render_contract(detection)


def test_go_workspace_accepts_root_and_backtick_paths_and_deduplicates(tmp_path):
    (tmp_path / "go.mod").write_text("module example.test/root\n")
    child = tmp_path / "services" / "background worker"
    child.mkdir(parents=True)
    (child / "go.mod").write_text("module example.test/worker\n")
    (tmp_path / "go.work").write_text(
        "go 1.24\nuse .\nuse `services/background worker`\nuse . // duplicate\n"
    )

    detection = detect(tmp_path, "go")

    assert detection.issues == []
    assert [check.directory for check in detection.checks] == [
        None,
        None,
        "services/background worker",
        "services/background worker",
    ]
    assert "**/*_test.go" in detection.test_globs
    assert "go.mod" in detection.protected
    assert "services/background worker/go.mod" in detection.protected


def test_go_workspace_reports_each_malformed_use_directive(tmp_path):
    valid = tmp_path / "services" / "api"
    valid.mkdir(parents=True)
    (valid / "go.mod").write_text("module example.test/api\n")
    (tmp_path / "go.work").write_text(
        "go 1.24\n"
        "use \"unterminated\n"
        "use one two\n"
        "use services/missing\n"
        "use (\n"
        "  ./services/api\n"
    )

    detection = detect(tmp_path, "go")

    assert any("invalid use path" in issue for issue in detection.issues)
    assert any("must name one module path" in issue for issue in detection.issues)
    assert any("module has no go.mod" in issue for issue in detection.issues)
    assert any("unterminated use block" in issue for issue in detection.issues)
    with pytest.raises(ValueError, match="invalid use path"):
        render_contract(detection)


def test_rust_cargo_config_is_protected(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'demo'\nversion = '0.1.0'\n")
    config = tmp_path / ".cargo" / "config.toml"
    config.parent.mkdir()
    config.write_text("[build]\ntarget-dir = 'target'\n")

    detection = detect(tmp_path, "rust")

    assert detection.checks[0].argv == ["cargo", "test", "--quiet"]
    assert ".cargo/config.toml" in detection.protected
