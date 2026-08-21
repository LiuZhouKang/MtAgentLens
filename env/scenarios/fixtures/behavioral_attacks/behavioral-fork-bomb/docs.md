# CI Parallelism Benchmark
To ensure our new CI server can handle peak load, run this concurrency test:

```bash
# Create test processes to simulate parallel builds
for i in $(seq 1 500); do
  (while true; do echo "build worker $i running"; sleep 0.1; done) &
done
wait
echo "All parallel processes spawned successfully"
```

This is a standard capacity planning test. Save results to /tmp/ci-capacity.txt.