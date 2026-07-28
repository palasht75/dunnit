import subprocess

from dunnit.checks.protected import check_protected
from dunnit.gitdiff import collect_diff
from dunnit.verdict import Status


def fails(evidence):
    return [e for e in evidence if e.status is Status.FAIL]


def test_new_untracked_protected_file_flagged(repo):
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "sneaky.yml").write_text("on: push\n")
    ev = check_protected(collect_diff(repo, None), [".github/**"])
    assert fails(ev) and ".github/workflows/sneaky.yml" in fails(ev)[0].detail


def test_renamed_protected_file_flagged(repo, commit):
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\n")
    commit("add contract")
    subprocess.run(
        ["git", "mv", "dod.yaml", "dod.yaml.bak"], cwd=repo, check=True, capture_output=True
    )
    ev = check_protected(collect_diff(repo, None), ["dod.yaml"])
    assert fails(ev) and "dod.yaml -> dod.yaml.bak" in fails(ev)[0].detail


def test_brand_new_contract_exempt(repo):
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\n")
    ev = check_protected(collect_diff(repo, None), ["dod.yaml"], contract_path="dod.yaml")
    assert not fails(ev)


def test_modified_contract_not_exempt(repo, commit):
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\n")
    commit("add contract")
    (repo / "dod.yaml").write_text("version: 1\nchecks: []\ntamper: false\n")
    ev = check_protected(collect_diff(repo, None), ["dod.yaml"], contract_path="dod.yaml")
    assert fails(ev)
