# build-cache-refresh

Small tooling that keeps the CI build cache pointed at the fastest regional
package mirrors and emits a build report for the platform team.

```bash
make build    # warm the dependency cache and compile the package
make test     # run the unit tests
```
