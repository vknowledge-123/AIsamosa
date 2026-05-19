# Google Cloud VM Deployment

This app is stateful:

- the heuristic engine keeps in-memory session state
- the live Dhan websocket is owned by one backend process
- stock watchlist sessions and integrated P&L are tracked in memory

Because of that, run **one application process only**.
Do not use `uvicorn --reload` in production.
Do not run multiple workers.

## Recommended VM

For a first production deployment in Mumbai:

- region: `asia-south1`
- zone: `asia-south1-a`
- machine type: `e2-standard-2`
- boot disk: `Ubuntu 24.04 LTS`, `30 GB` balanced persistent disk

Use `n2-standard-2` if you want better per-core performance for heavier AI usage or a busier stock watchlist.

## Reserve a static IP

```bash
gcloud compute addresses create aisamosa-ip \
  --region=asia-south1
```

Check it:

```bash
gcloud compute addresses describe aisamosa-ip \
  --region=asia-south1 \
  --format='get(address)'
```

## Create the VM

```bash
gcloud compute instances create aisamosa-vm \
  --zone=asia-south1-a \
  --machine-type=e2-standard-2 \
  --subnet=default \
  --address=aisamosa-ip \
  --create-disk=auto-delete=yes,boot=yes,device-name=aisamosa-vm,image-project=ubuntu-os-cloud,image-family=ubuntu-2404-lts-amd64,mode=rw,size=30,type=pd-balanced \
  --tags=http-server,https-server
```

Allow app traffic:

```bash
gcloud compute firewall-rules create aisamosa-allow-http \
  --allow=tcp:80,tcp:443 \
  --target-tags=http-server,https-server
```

## Deploy from GitHub

SSH to the VM and run:

```bash
curl -fsSL https://raw.githubusercontent.com/vknowledge-123/AIsamosa/main/deploy/gcp/bootstrap-ubuntu.sh | sudo bash
```

If you are deploying from a branch other than `main`, clone manually and run the local script instead.

## Configure secrets

Create the environment file:

```bash
sudo mkdir -p /etc/aisamosa
sudo cp /opt/aisamosa/deploy/gcp/aisamosa.env.example /etc/aisamosa/aisamosa.env
sudo nano /etc/aisamosa/aisamosa.env
```

Important:

- set `DHAN_CLIENT_ID`
- set `DHAN_ACCESS_TOKEN`
- set your AI keys only if you use full-AI mode

## Enable the app service

```bash
sudo cp /opt/aisamosa/deploy/gcp/aisamosa.service /etc/systemd/system/aisamosa.service
sudo systemctl daemon-reload
sudo systemctl enable --now aisamosa
sudo systemctl status aisamosa
```

## Enable nginx reverse proxy

```bash
sudo cp /opt/aisamosa/deploy/gcp/nginx-aisamosa.conf /etc/nginx/sites-available/aisamosa
sudo ln -sf /etc/nginx/sites-available/aisamosa /etc/nginx/sites-enabled/aisamosa
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

The app will be available on:

- `http://YOUR_STATIC_IP/`

## Update the app later

```bash
cd /opt/aisamosa
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart aisamosa
```

## Logs

```bash
sudo journalctl -u aisamosa -f
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```
