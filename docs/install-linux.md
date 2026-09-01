# Ubuntu/Linux installation

For Phase 2, size TEMP_PATH for complete active representations; see [transcoding](transcoding.md).
The API owns conversion supervisors and requires writable isolated temp storage,
while source media stays read-only. Use one API process and the existing worker.
GPU mappings/drivers are optional reviewed overrides, not a reason to enable
privileged mode or remove the private internal network. Confirm a successful test
encode in System Health before claiming hardware acceleration.

Run the [workstation checklist](phase2-validation.md) after container builds and
migrations. Native development tests are not a substitute for Compose validation.

## Requirements

- A currently supported Ubuntu release or comparable Linux distribution
- Docker Engine from a trusted package source
- Docker Compose v2 (`docker compose`, not legacy `docker-compose`)
- A user permitted to access the Docker socket
- A readable media directory and writable state/backup directories

Follow Docker's distribution-specific Engine installation documentation. The BlueReel script intentionally does not add repositories, install packages, start system services, add users to privileged groups, change firewall rules, or use `sudo`. If elevation or socket permission is needed, it reports the exact class of blocker so the operator can apply local policy.

Run the bootstrap as the unprivileged account that will own application state, not through `sudo`; UID/GID zero is rejected so the application containers do not become root. Resolve Docker socket access according to local policy before rerunning.

Verify first:

```sh
docker version
docker compose version
docker info
```

Membership in the `docker` group is effectively root-equivalent; use it only if that matches the host's security policy. Rootless Docker is also suitable when bind-mount permissions are configured correctly.

## Install BlueReel

From the repository:

```sh
sh scripts/bootstrap.sh --media-path /srv/media
```

The media directory must exist and be readable/traversable. Omit `--media-path` to create an empty local `media/` directory. The script writes the invoking user's numeric UID/GID to a newly created `.env`, which keeps bind-mounted state owned by that user. It preserves an existing `.env` without modification.

The script validates state directory separation, blocks root/repository-wide state targets, rejects writable state nested in source media, renders Compose, builds the containers, starts the project, and waits for `http://localhost:8080/api/v1/health/ready`.

If the repository was copied without executable bits, using `sh scripts/bootstrap.sh` as shown remains valid. A normal Git checkout should preserve them.

## Owner setup and container paths

Open [http://localhost:8080](http://localhost:8080), create the initial Owner, and use paths below `/media`. For example, `/srv/media/Movies` on the host is `/media/Movies` in BlueReel.

Do not use the host path in an API request; the backend validates against container-visible allowed roots. Source media is mounted read-only even if the host account can write it.

## Service management

BlueReel containers use `restart: unless-stopped`, but Docker itself must be configured to start according to local policy.

```sh
docker compose ps
docker compose logs --tail 200
docker compose down
docker compose up --detach
```

Compose manages only the `bluereel` project. The scripts do not prune images, stop other projects, or alter other workloads.

## Private-LAN access

The default is loopback only. To serve trusted household devices, place this host's stable RFC1918 address in `BIND_ADDRESS` and restart. Do not use a wildcard/public address. The script does not touch UFW, nftables, a cloud security group, or router forwarding; any narrow private-network firewall rule is an operator decision.

BlueReel does not provide TLS, an Internet gateway, or remote-access hardening in this phase. Keep it off untrusted networks.
