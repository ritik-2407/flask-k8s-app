# Flask + MongoDB on Kubernetes (Minikube)

A small Flask app (`/` and `/data`) backed by an **authenticated** MongoDB, deployed on Minikube with
persistent storage, a Horizontal Pod Autoscaler (HPA), and resource requests/limits.

Everything in this README was run and verified on: Windows 11, Docker Desktop (Docker 28.5.1),
Minikube v1.39.0 (Docker driver, Kubernetes v1.37.0), kubectl v1.34.1.

## Contents

1. [Architecture](#architecture)
2. [Repository layout](#repository-layout)
3. [Prerequisites](#prerequisites)
4. [Build and push the image](#1-build-and-push-the-image)
5. [Start Minikube](#2-start-minikube)
6. [Deploy](#3-deploy)
7. [Access the app](#4-access-the-app)
8. [Local development (Part 1)](#local-development-part-1)
9. [How MongoDB authentication works](#how-mongodb-authentication-works)
10. [DNS resolution inside the cluster](#dns-resolution-inside-the-cluster)
11. [Resource requests and limits](#resource-requests-and-limits)
12. [Design choices](#design-choices)
13. [Testing scenarios and results](#testing-scenarios-and-results)
14. [Issues encountered](#issues-encountered)
15. [Known limitations](#known-limitations)
16. [Cleanup](#cleanup)

## Architecture

```
Your machine
   |  minikube service (tunnel to NodePort 30080)
   v
[flask-service  NodePort :5000]
   |  load-balances across pods
   v
[flask-app pods: 2..5, scaled by the HPA at 70% CPU]
   |  connects to host "mongodb" (resolved by CoreDNS)
   v
[mongodb Service  ClusterIP :27017]   <- reachable only inside the cluster
   v
[mongodb-0 pod (StatefulSet, auth enabled)] --> PVC mongo-pvc --> PV mongo-pv (/data/db)
```

## Repository layout

```
app.py                         Flask app (MongoDB auth via env vars)
requirements.txt               Pinned dependencies (as given by the assignment)
Dockerfile                     Image for the Flask app
.dockerignore / .gitignore     Keep junk and secrets out of the image / repo
.env.example                   Env var for local development
k8s/00-secret.yaml             MongoDB credentials (sample values)
k8s/01-mongo-pv-pvc.yaml       PersistentVolume + PersistentVolumeClaim
k8s/02-mongo-statefulset.yaml  MongoDB StatefulSet (auth enabled)
k8s/03-mongo-service.yaml      MongoDB ClusterIP Service
k8s/04-flask-deployment.yaml   Flask Deployment (2 replicas)
k8s/05-flask-service.yaml      Flask NodePort Service
k8s/06-flask-hpa.yaml          HPA (70% CPU, 2-5 replicas)
k8s/load-generator.yaml        Load generator, used only for the autoscaling test
test-results/                  Raw output captured during the autoscaling test
```

## Prerequisites

Docker (running), Minikube, kubectl, and a Docker Hub account (or any registry).
Docker Desktop needs at least ~3.5 GB of memory assigned (Settings > Resources).

## 1. Build and push the image

The image name used throughout is `ritikk2407/flask-mongodb-app:1.0`. To use your own account,
replace `ritikk2407` here **and** in `k8s/04-flask-deployment.yaml`.

```bash
docker build -t ritikk2407/flask-mongodb-app:1.0 .
docker login
docker push ritikk2407/flask-mongodb-app:1.0
```

- The Docker Hub repository must be **public**, otherwise the pods fail with `ImagePullBackOff`
  (a private repo would need an `imagePullSecret`).
- Any registry works (GHCR, ECR, ...): use its full path as the image name.
- Use a versioned tag (`1.0`), not `latest`, so a deployment always refers to a known build.

## 2. Start Minikube

Five Flask pods plus MongoDB need headroom, otherwise scaled-up pods stay `Pending`.
The pods request about 1.2 CPU and 1.5 GB in total at the maximum of 5 Flask replicas.

```bash
minikube start --cpus=4 --memory=3500
minikube addons enable metrics-server   # required by the HPA
```

`--memory=3500` is used because Docker Desktop on the test machine was limited to ~3.8 GB, and
Minikube refuses a value above what Docker has. If your Docker Desktop has more memory, `4096` is fine.

## 3. Deploy

The files are numbered in apply order.

```bash
kubectl apply -f k8s/00-secret.yaml
kubectl apply -f k8s/01-mongo-pv-pvc.yaml
kubectl apply -f k8s/02-mongo-statefulset.yaml
kubectl apply -f k8s/03-mongo-service.yaml
kubectl wait --for=condition=ready pod -l app=mongodb --timeout=180s
kubectl apply -f k8s/04-flask-deployment.yaml
kubectl apply -f k8s/05-flask-service.yaml
kubectl apply -f k8s/06-flask-hpa.yaml
kubectl get all,pv,pvc,hpa
```

Do not run `kubectl apply -f k8s/` as a shortcut: it would also start `load-generator.yaml`,
which is only for the autoscaling test. (The first MongoDB start takes ~1 minute because the image
is pulled.)

What each step does:

| File | Effect |
|---|---|
| `00-secret.yaml` | Stores the MongoDB username/password in the cluster. |
| `01-mongo-pv-pvc.yaml` | Reserves 1 Gi of storage and a claim for it. Both should show `Bound`. |
| `02-mongo-statefulset.yaml` | Starts `mongodb-0` with authentication enabled, data on the PVC. |
| `03-mongo-service.yaml` | Gives MongoDB a stable, cluster-internal address named `mongodb`. |
| `04-flask-deployment.yaml` | Starts 2 Flask pods that connect to `mongodb` with the Secret credentials. |
| `05-flask-service.yaml` | Stable address for Flask, load-balanced across pods, exposed via NodePort. |
| `06-flask-hpa.yaml` | Scales Flask between 2 and 5 pods at 70% average CPU. |

## 4. Access the app

On Windows/macOS with the Docker driver the NodePort is not directly reachable from the host.
Use the Minikube tunnel and **keep that terminal open**:

```bash
minikube service flask-service --url     # prints e.g. http://127.0.0.1:62616 (port changes each run)
```

In another terminal, using the printed URL:

```bash
curl <URL>/
curl -X POST -H "Content-Type: application/json" -d '{"sampleKey":"sampleValue"}' <URL>/data
curl <URL>/data
```

Windows notes: the `curl` POST above uses bash quoting and will **not** work as-is in `cmd.exe` or
PowerShell. Sending it unchanged returns `{"error":"Request body must be valid JSON"}`, because
those shells mangle the JSON quotes before curl sees them (the app is behaving correctly).

```bat
:: cmd.exe: double-quote the body and escape the inner quotes
curl -X POST -H "Content-Type: application/json" -d "{\"sampleKey\":\"sampleValue\"}" <URL>/data
```

```powershell
# PowerShell: use curl.exe (plain `curl` is an alias for Invoke-WebRequest), or Invoke-RestMethod
curl.exe -X POST -H "Content-Type: application/json" -d '{\"sampleKey\":\"sampleValue\"}' <URL>/data
Invoke-RestMethod -Method Post -Uri <URL>/data -ContentType application/json -Body '{"sampleKey":"sampleValue"}'
```

In Git Bash, paths such as `/etc/resolv.conf`
get rewritten to Windows paths: run `export MSYS_NO_PATHCONV=1` first when passing absolute
Linux paths to `kubectl exec`.

The time in the welcome message is **UTC** (the container timezone), not your local time.

## Local development (Part 1)

```bash
python -m venv venv
source venv/bin/activate                 # Windows: venv\Scripts\activate
pip install -r requirements.txt
docker run -d -p 27017:27017 --name mongodb mongo:latest
export MONGODB_URI=mongodb://localhost:27017/     # Windows cmd: set MONGODB_URI=...
python app.py                                      # listens on http://localhost:5000
```

This was verified on Python 3.12 (the Dockerfile uses Python 3.9; both work with the pinned
Flask 2.0.2 / Werkzeug 2.0.3 / pymongo 3.12.0). Remove the test container afterwards with
`docker rm -f mongodb`.

**Why use a virtual environment?**

- **Isolation:** each project gets its own packages, so two projects needing different versions of
  the same library do not conflict (this project pins old versions on purpose).
- **Reproducibility:** `pip install -r requirements.txt` rebuilds the same environment anywhere.
- **Clean system Python:** nothing is installed globally, and no admin rights are needed.
- **Easy to reset:** delete the `venv/` folder and recreate it.

## How MongoDB authentication works

- The credentials live in a Kubernetes **Secret** (`mongo-credentials`), not in the image or the
  application code.
- The official `mongo` image reads `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` on
  first start, creates that root user, and starts `mongod` with authorization enabled. Confirmed
  in the MongoDB log: `"security":{"authorization":"enabled"}`.
- The Flask pods receive the same Secret values as env vars (`MONGO_USERNAME`, `MONGO_PASSWORD`).
  `app.py` builds `mongodb://user:pass@mongodb:27017/?authSource=admin` (credentials URL-encoded so
  special characters cannot break the URI). `authSource=admin` is needed because the root user is
  created in the `admin` database.
- `MONGODB_URI`, if set, overrides this (used for the local, unauthenticated Part 1 setup).
- **Caveat:** the init variables only apply when the data directory is empty. Changing the Secret
  later does not change the password of an existing database (see Issues encountered).
- **Caveat:** Secrets are base64-encoded, not encrypted. `kubectl get secret ... -o jsonpath=... |
  base64 -d` returns the password to anyone with read access. The Secret file here holds sample
  values for the assignment only. In a real setup, create it out-of-band (`kubectl create secret`),
  or use Sealed Secrets / External Secrets / Vault, and enable encryption at rest.
- **Caveat:** Flask uses the root user for simplicity. Production would create an application user
  with `readWrite` on `flask_db` only (least privilege).

## DNS resolution inside the cluster

Pods should never talk to each other by IP: pod IPs change whenever a pod is recreated. Instead:

1. **Every Service gets a DNS record**, created automatically by **CoreDNS** (the cluster DNS
   server, running in the `kube-system` namespace). The full name is:

   ```
   <service>.<namespace>.svc.cluster.local     ->   mongodb.default.svc.cluster.local
   ```

2. **Every pod is configured to use CoreDNS.** The kubelet writes the pod's `/etc/resolv.conf`.
   Real output from a Flask pod in this cluster:

   ```
   search default.svc.cluster.local svc.cluster.local cluster.local
   nameserver 10.96.0.10
   options ndots:5
   ```

   `10.96.0.10` is the ClusterIP of the `kube-dns` Service in front of CoreDNS. The `search` line
   lets short names work: `mongodb` is tried as `mongodb.default.svc.cluster.local` first.
   (`ndots:5` means names with fewer than 5 dots go through the search list before being tried
   as absolute names.)

3. **The Service IP is stable and load-balances.** CoreDNS answers with the Service's ClusterIP.
   `kube-proxy` then forwards traffic on that IP to a healthy pod matching the Service selector
   (`app=mongodb`).

Flow: Flask pod asks for `mongodb` -> CoreDNS returns the ClusterIP -> traffic to
ClusterIP:27017 is forwarded by kube-proxy to the `mongodb-0` pod.

Verified in this cluster (all three names resolve to the same Service IP):

```
mongodb                              -> 10.111.227.104
mongodb.default                      -> 10.111.227.104
mongodb.default.svc.cluster.local    -> 10.111.227.104
```

Reproduce it:

```bash
kubectl exec deploy/flask-app -- python -c "import socket; print(socket.gethostbyname('mongodb'))"
kubectl get svc mongodb          # CLUSTER-IP matches the address above
```

**Configuring Flask to connect:** the Deployment sets `MONGO_HOST=mongodb` (the Service name) and
`MONGO_PORT=27017`; `app.py` uses them to build the connection string. Rules of thumb:
same namespace -> short name `mongodb`; other namespace -> `mongodb.<namespace>` or the full name.
Never use `localhost` (that is the Flask pod itself) or a pod IP.

## Resource requests and limits

- **Request:** the amount of CPU/memory the scheduler *reserves* for the container. A pod is only
  placed on a node with that much unreserved capacity, so requests drive scheduling. They are
  also the baseline for the HPA: "70% CPU utilization" means 70% of the *request*.
- **Limit:** the hard ceiling. CPU above the limit is **throttled** (the container slows down but
  lives). Memory above the limit gets the container **OOMKilled** and restarted, because memory
  cannot be throttled.
- **Why both:** requests make scheduling predictable and capacity planning honest; limits stop one
  misbehaving pod from starving its neighbours or crashing the node.
- **QoS class:** pods with requests lower than limits are `Burstable` (confirmed with
  `kubectl get pod -o jsonpath='{.status.qosClass}'` for both Flask and MongoDB). Under node
  memory pressure, Burstable pods are evicted before `Guaranteed` ones (requests = limits) but
  after `BestEffort` ones (nothing set).
- **Without requests**, the HPA cannot compute a utilization percentage and shows `<unknown>`.

Values used for both Flask and MongoDB, exactly as the assignment specifies:

| | CPU | Memory |
|---|---|---|
| Request | `200m` (0.2 CPU) | `250M` |
| Limit | `500m` (0.5 CPU) | `500M` |

(`M` is megabytes, 10^6 bytes; `Mi` is mebibytes, 2^20 bytes. `Mi` is the more common Kubernetes
unit, but `M` is what the assignment states, so it is used here.)

With a 200m request, the HPA target of 70% = **140m average CPU per pod**.

## Design choices

| Decision | Chosen | Alternatives considered | Reason |
|---|---|---|---|
| Flask workload | Deployment, 2 replicas | Bare Pods, ReplicaSet | Flask is stateless; a Deployment gives rolling updates and self-healing, and the HPA targets it. Bare pods are not recreated if they die. |
| MongoDB workload | StatefulSet, 1 replica | Deployment | Databases need stable identity (`mongodb-0`) and stable storage. The assignment also asks for a StatefulSet. |
| MongoDB replicas | 1 | 3-member replica set | The assignment asks for a single PV/PVC, and one PV cannot back several mongod pods. A replica set is the production answer (per-pod PVCs via `volumeClaimTemplates`, keyfile auth, an init job) but is out of scope. Trade-off: MongoDB is a single point of failure. |
| MongoDB Service | ClusterIP | NodePort, LoadBalancer, headless | The requirement is "accessible only within the cluster". ClusterIP has no external IP. A headless Service (`clusterIP: None`) is the usual companion for multi-replica StatefulSets, where each pod needs its own DNS name; with one replica it adds nothing. |
| Flask Service | NodePort | LoadBalancer, Ingress, port-forward | Simplest way to reach the app from the host on Minikube. LoadBalancer needs `minikube tunnel`; Ingress adds a controller for no benefit with one app; `kubectl port-forward` bypasses the Service and load balancing, so it would not exercise what we want to test. |
| Storage | Manually created hostPath PV + PVC (`storageClassName: manual`) | `volumeClaimTemplates` with dynamic provisioning (Minikube's default StorageClass) | The assignment explicitly asks for a PV and PVC. hostPath is fine on single-node Minikube (the node's `/data` directory persists across pod restarts and `minikube stop/start`) but ties data to one node, so it is not for multi-node production. |
| PV reclaim policy | `Retain` | `Delete` | Deleting the claim must never silently delete database files. Downside: leftover data keeps old credentials (see Issues). |
| Credentials | Secret injected as env vars | Hard-coded in YAML/image, ConfigMap | Keeps credentials out of the image and the Deployment spec, and one Secret feeds both MongoDB and Flask. Env vars are simpler than mounted files here. Secrets are only base64, not encrypted. |
| Autoscaling signal | CPU, 70%, min 2, max 5 | Memory, custom/request-rate metrics | Required by the assignment. Needs metrics-server. A CPU request on the pod is required for the percentage to be computed. |
| MongoDB version | `mongo:6.0` (pinned) | `mongo:latest`, 5.0, 4.4 | A pinned version is reproducible; `latest` can change under you. 6.0 worked with the pinned pymongo 3.12.0 (verified: reads and writes OK). Mongo 5.0+ needs a CPU with AVX; `4.4` is the fallback if the container crash-loops on an old machine. |
| Image | `python:3.9-slim`, non-root user | Full python image, alpine | Small, compatible with Flask 2.0.2 / Werkzeug 2.0.3, and safer as non-root (verified `uid=1000(appuser)`). Alpine often needs extra build steps for Python packages. |
| App server | Flask dev server (as provided) | gunicorn | Kept as given by the assignment. For production use gunicorn with several workers. The dev server is also why 4 load pods could saturate Flask so easily in the test. |
| Probes | HTTP GET `/` for readiness and liveness | Probe that also checks MongoDB | `/` does not touch the database, so a Flask pod can be Ready while MongoDB is down (requests to `/data` would then fail). A `/healthz` that pings MongoDB would be better, but a DB-coupled liveness probe can cause restart storms during a DB outage. |
| Namespace | `default` | Dedicated namespace | Keeps the commands short for the assignment. In production use a namespace per app/environment. |

## Testing scenarios and results

Raw output is in `test-results/`.

### 1. Application and database interactions

Through the Minikube tunnel from the host:

```
GET  /                  -> Welcome to the Flask app! The current time is: 2026-11-12 10:39:53.655386
POST /data (sampleKey)  -> {"status":"Data inserted"}
GET  /data              -> [{"from":"k8s"},{"sampleKey":"sampleValue"}]
POST /data (not JSON)   -> {"error":"Request body must be valid JSON"}   (HTTP 400)
```

### 2. Authentication

MongoDB rejects unauthenticated and wrongly authenticated access, and accepts the right credentials:

```
$ kubectl exec mongodb-0 -- mongosh --quiet --eval "db.getSiblingDB('flask_db').data.find().toArray()"
MongoServerError: command find requires authentication

$ kubectl exec mongodb-0 -- mongosh --quiet -u admin -p wrong --authenticationDatabase admin --eval "db.runCommand({ping:1})"
MongoServerError: Authentication failed.

$ kubectl exec mongodb-0 -- mongosh --quiet -u admin -p '<password>' --authenticationDatabase admin --eval "db.getSiblingDB('flask_db').data.find({},{_id:0}).toArray()"
[ { from: 'k8s' }, { sampleKey: 'sampleValue' } ]
```

The `mongodb` Service is `ClusterIP` with no external IP, so it is not reachable from outside the cluster.

### 3. Persistence

Deleted the MongoDB pod, waited for the StatefulSet to recreate and re-attach the volume, then read the data again:

```bash
kubectl delete pod mongodb-0
kubectl wait --for=condition=ready pod/mongodb-0 --timeout=120s
curl <URL>/data
```

Result: both records were still returned (`[{"from":"k8s"},{"sampleKey":"sampleValue"}]`),
and Flask reconnected without being restarted, because it addresses MongoDB by the Service name.

### 4. Autoscaling under load

**Result:** under load, average CPU rose to 226% of the request (target 70%) and the HPA scaled the
Flask Deployment from **2 to 5 replicas in under 35 seconds** (4 pods at the 16:19:30 snapshot, 5 at
16:19:46; the previous snapshot, 16:19:14, was still at 2 pods), then held at the `maxReplicas` cap
of 5. After the load was removed, CPU fell to 1% and the HPA scaled back
**from 5 to 2** after the default 5-minute stabilization window.

![HPA autoscaling test: CPU and replica count over time](test-results/hpa-autoscaling.png)

*Chart generated from the `kubectl` snapshots in `test-results/hpa-timeline.txt` (taken every
15 seconds) with `test-results/plot_hpa.py`. It is a plot of the captured data, not a screenshot.
The "load removed" line is approximate; the CPU metric lags the real change by roughly a minute.*

Setup: HPA at 70% CPU, min 2, max 5 (140m average per pod at a 200m request).
Load: `k8s/load-generator.yaml`, 4 busybox pods each running a tight `wget` loop against
`http://flask-service:5000/`. Run it with:

```bash
kubectl get hpa flask-hpa -w            # terminal 1
kubectl get pods -l app=flask-app -w    # terminal 2
kubectl apply -f k8s/load-generator.yaml
# ... observe, then:
kubectl delete -f k8s/load-generator.yaml
```

Timeline (from `test-results/hpa-timeline.txt`):

| Time | CPU / target | Replicas | Event |
|---|---|---|---|
| 16:17:56 | 1% / 70% | 2 | Load applied. |
| 16:18:27 | 24% / 70% | 2 | Load builds up. |
| 16:19:30 | 226% / 70% | 4 | HPA scales 2 -> 4, then 4 -> 5 about 15 s later. |
| 16:21:21 | 117% / 70% | 5 | Stuck at `maxReplicas`; the HPA never exceeds 5. |
| 16:23:27 | 24% / 70% | 5 | CPU starts falling after the load is removed (metric lags). |
| 16:24:29 | 1% / 70% | 5 | CPU is back to idle; replicas stay at 5 during the stabilization window. |
| 16:28:36 | 1% / 70% | 2 | HPA scales 5 -> 2 ("All metrics below target"). |

HPA events (`test-results/hpa-rescale-events.txt`):

```
SuccessfulRescale  New size: 4; reason: cpu resource utilization (percentage of request) above target
SuccessfulRescale  New size: 5; reason: cpu resource utilization (percentage of request) above target
SuccessfulRescale  New size: 2; reason: All metrics below target
```

At 5 replicas, `kubectl top pods` showed each Flask pod at 232-237m CPU (about 116-119% of its
200m request, below the 500m limit) and each load pod at ~370m, so even the maximum capacity was
saturated by the load generator. The scale-up was immediate; the scale-down was deliberately slow
(the default 300 s stabilization window prevents flapping). Pods stayed `Ready` and no pod went
`Pending` during scale-up.

Note: this test was run with the memory request/limit written as `250Mi`/`500Mi`, and the files were
later changed to the assignment's `250M`/`500M`. Only memory changed; the CPU request (200m) that
the HPA uses was identical, so the result still applies. Both versions were deployed and ran fine.

## Issues encountered

1. **HPA showed `<unknown>` for the first ~3 minutes** (`FailedGetResourceMetric`: "no metrics
   returned from resource metrics API", then "pods might be unready"). The metrics-server pod
   needed time to become ready and scrape the new pods. It resolved by itself; wait until
   `kubectl top pods` returns numbers before testing the HPA. The warning events remain in
   `test-results/hpa-events.txt`.
2. **NodePort not reachable from Windows with the Docker driver.** The Minikube node is a
   container, so its IP is not routable from the host. Fixed with
   `minikube service flask-service --url`, which opens a tunnel; the terminal must stay open and
   the port changes each run.
3. **Memory setting.** Docker Desktop was limited to ~3.8 GB, so `minikube start --memory=4096`
   would be refused. Using `--memory=3500` worked.
4. **First MongoDB start takes ~1 minute** (`ContainerCreating`) while the image downloads.
   This is normal, which is why the deploy steps `kubectl wait` for MongoDB before starting Flask.
5. **kubectl/cluster version skew.** Minikube warned that kubectl 1.34 is older than the 1.37
   cluster. Kubernetes only guarantees one minor version of skew, but every command used here
   worked. `minikube kubectl --` is a version-matched fallback.
6. **A leftover local Flask process from Part 1** kept answering on port 5000 after the terminal
   was closed (two `python.exe` processes were still running). Found with
   `Get-CimInstance Win32_Process` and stopped. Not a cluster issue, but it can confuse local testing.
7. **Git Bash rewrites Linux paths.** `kubectl exec ... cat /etc/resolv.conf` returned
   `C:/Program Files/Git/etc/resolv.conf: No such file`. Fixed with `export MSYS_NO_PATHCONV=1`.
8. **Pod clock is UTC**, so the welcome message is 5.5 hours behind IST. Not changed; it is
   the correct behaviour for containers by default.
9. **`POST /data` returned "Request body must be valid JSON" from `cmd.exe`.** The bash-style
   `-d '{"sampleKey":"sampleValue"}'` is not valid in `cmd.exe`: single quotes are literal and the
   inner double quotes are stripped, so Flask received malformed JSON. Fixed by using the
   `cmd.exe` quoting shown in section 4. The 400 response was the app's input validation working.
10. **Gotcha, not hit but worth knowing:** the MongoDB init credentials only apply to an empty data
    directory, and the PV uses `Retain`. If you change the Secret after the first deployment and
    redeploy, MongoDB keeps the old password and Flask gets "Authentication failed". Fix by wiping
    the data (`minikube delete`) or changing the password inside MongoDB.
## Known limitations

- Single MongoDB instance: no high availability, and `hostPath` ties data to one node.
- Root database user and a plaintext sample Secret committed to the repo, for the assignment only.
- Flask dev server, not a production WSGI server.
- Readiness/liveness probes do not check the database.
- No NetworkPolicy: any pod in the cluster can reach MongoDB (it still needs the password).
- No TLS between Flask and MongoDB.
- Everything runs in the `default` namespace.

## Cleanup

```bash
kubectl delete -f k8s/ --ignore-not-found     # removes all resources (incl. the PV and load generator)
minikube delete                               # removes the cluster and the hostPath data
```
