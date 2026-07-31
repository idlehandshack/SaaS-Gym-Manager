3. Production on the droplet — replace Gunicorn with Daphne

Since you're manually managing this (not Render's buildpack), you're likely running Gunicorn via systemd + Nginx reverse proxy. You need to swap the app server to Daphne (ASGI), since Gunicorn's default sync workers can't handle WebSockets.

Find your current systemd service (likely /etc/systemd/system/gunicorn.service or similar) and change the ExecStart line:

ini
[Unit]
Description=EnterGYM Daphne ASGI Server
After=network.target

[Service]
User=your_deploy_user
Group=www-data
WorkingDirectory=/path/to/your/project
Environment="PATH=/path/to/your/venv/bin"
ExecStart=/path/to/your/venv/bin/daphne -b 127.0.0.1 -p 8000 Fitness.asgi:application

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target

Then:

bash
sudo systemctl daemon-reload
sudo systemctl restart gunicorn   # or whatever you renamed it
sudo systemctl status gunicorn
4. Nginx — add WebSocket upgrade headers

Your existing Nginx config proxies HTTP to port 8000. Add a location block (or extend the existing one) so /ws/ requests get the Upgrade headers WebSocket needs:

nginx
server {
    listen 443 ssl;
    server_name entergym.in *.entergym.in;

    # ... your existing SSL cert config ...

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_header;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;  # keep long-lived connections alive
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_header;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

Reload Nginx:

bash
sudo nginx -t && sudo systemctl reload nginx
5. Redis — you're already covered

Since your CHANNEL_LAYERS reuses REDIS_URL, and you're already running Redis on the droplet for caching, there's nothing new to provision — same instance handles both.

One resource concern worth flagging

A $6 droplet is typically 1 vCPU / 1GB RAM. Daphne itself is lightweight, but running Daphne + Redis + Postgres/SQLite + Gunicorn-replaced-worker all on 1GB can get tight under load, especially with multiple gyms' staff dashboards holding persistent WebSocket connections open simultaneously. Worth keeping an eye on htop / free -m after this goes live — if it's a squeeze, a single-worker Daphne setup (which is what's shown above) is the right conservative starting point; you can scale to daphne -w 2 or a Uvicorn+workers setup later if needed.