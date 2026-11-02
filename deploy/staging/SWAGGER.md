# Staging Swagger UI (SSH only)

The staging FastAPI container publishes its API only to the server's loopback interface (`127.0.0.1`). No public firewall rule or domain is required.

1. On **your local computer** (PowerShell or terminal), open an SSH tunnel:

   ```bash
   ssh -N -L 18000:127.0.0.1:18000 kuai@YOUR_SERVER_IP
   ```

   Use your existing SSH host alias or `-p PORT` if your server uses a custom SSH port. Leave this terminal open while testing.

2. In **your local browser**, open [http://localhost:18000/docs](http://localhost:18000/docs).

3. Expand `POST /api/v1/chat/query`, choose **Try it out**, and submit:

   ```json
   {"query": "지급할 수 있는 장학금에 뭐가 있는지 알려줘"}
   ```

4. Close the tunnel with `Ctrl+C`.

If local port 18000 is occupied, use `ssh -N -L 18080:127.0.0.1:18000 ...` and browse to `http://localhost:18080/docs`.

To verify that the server is not listening publicly, on the **server** run:

```bash
docker ps --filter name=chatbot-staging-api
ss -ltn | grep ':18000'
curl -I http://127.0.0.1:18000/docs
```

The host binding should be `127.0.0.1:18000`. Production Compose is intentionally unchanged. The staging Compose file must be deployed via the normal staging pipeline before this URL works; do not manually publish the port to `0.0.0.0`.
