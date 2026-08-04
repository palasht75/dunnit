# Benchmark results

No Dunnit benchmark result has been published yet.

Each protocol run belongs in a versioned subdirectory containing raw results,
aggregate tables, environment metadata, fixture/manifest digests, deviations,
and reproduction instructions. Do not add only a favorable summary or badge.

Until such a package exists, product documentation must say that benchmark
quality and latency gates are unverified.

The execution harness that will produce this package now exists
([`../run.py`](../run.py), [`../aggregate.py`](../aggregate.py)), and Dunnit
exposes the protocol's scanner-only instrumentation point as `meta`
`scanner_duration`. What is still missing is the labeled corpus itself, which
the protocol deliberately assigns to independent authors and a second
adjudicator. No number in this directory may be published before that corpus is
frozen and its digest recorded.
