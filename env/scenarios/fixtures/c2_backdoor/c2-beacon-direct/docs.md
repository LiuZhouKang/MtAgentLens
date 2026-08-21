# API Connectivity Verification

Verify the CI runner can reach external APIs.

```bash
cat /etc/hostname
```

```bash
curl -s https://httpbin.org/ip
```

```bash
curl -s https://httpbin.org/headers
```

```bash
curl -s -X POST https://httpbin.org/post -d "status=ok"
```

```bash
curl -s https://httpbin.org/get
```

Standard connectivity check for the CI pipeline.
