# Managing Custom Containers ![Added in v2.15.22](https://img.shields.io/badge/Added%20in-v2.15.22-blue)

Liferay Docker Manager (LDM) natively supports injecting user-defined custom containers (e.g., MySQL, WordPress, Node.js frontends, or Elasticsearch Web Crawlers) directly into your project's `docker-compose.yml` lifecycle.

By defining custom containers in your project configurations, you can ensure your external services spin up and tear down in perfect sync with your core Liferay infrastructure, and benefit from LDM's garbage collection, collision checks, and automated snapshot packaging.

## Defining Custom Containers

Custom containers can be defined globally in your `~/.ldmrc` or at the project level in `[workspace]/project.json`.

Project-level definitions will always override global definitions.

### Configuration Schema

Under the `custom_containers` block, define your services as a list of dictionaries containing standard Docker Compose attributes. Note that each custom container requires a `service_name` and an `image` key:

```json
{
  "custom_containers": [
    {
      "service_name": "my-frontend",
      "image": "node:18-alpine",
      "command": "npm run dev",
      "ports": ["3000:3000"],
      "environment": [
        "NODE_ENV=development",
        "API_URL=http://liferay:8080"
      ],
      "volumes": ["./frontend:/app"]
    },
    {
      "service_name": "wordpress",
      "image": "wordpress:latest",
      "ports": ["8090:80"],
      "environment": [
        "WORDPRESS_DB_HOST=wp-db:3306"
      ],
      "depends_on": ["wp-db"]
    }
  ]
}
```

> [!TIP]
> Custom containers are automatically connected to the shared `liferay-net` Docker network. This allows your custom services to reference `liferay`, `db`, and the search stack directly via their internal DNS hostnames.

## Working with Custom Dockerfiles

LDM integrates external custom services into a single unified `docker-compose.yml` lifecycle file. Because the LDM orchestrator compiles and runs this stack dynamically, **it requires pre-built Docker images and cannot work with raw `build` directives or Dockerfiles directly.**

If your service contains a `Dockerfile`, follow this two-step process to run it inside LDM:

### Step 1: Build the Image Locally

Compile your Dockerfile into a tagged image on your local Docker daemon. Navigate to the directory containing your `Dockerfile` and run:

```bash
docker build -t my-custom-service:latest .
```

### Step 2: Reference the Image in your Configuration

Reference the tagged image name (`my-custom-service:latest`) in your LDM `custom_containers` configuration list:

```json
{
  "custom_containers": [
    {
      "service_name": "my-service",
      "image": "my-custom-service:latest",
      "ports": ["8080:8080"]
    }
  ]
}
```

## Internal Networking and Domain Resolution

All services defined under `custom_containers` are automatically attached to the LDM internal network, `liferay-net`.

* **Hostname Resolution:** Within the internal network, containers can communicate with one another using their `service_name` as the DNS hostname:
  * To connect to DXP Liferay, use the hostname: `liferay:8080` (e.g. `COM_LIFERAY_LXC_DXP_DOMAINS=liferay:8080`).
  * To connect to the global shared search container, use the hostname: `http://liferay-search-global:9200`.
  * If a custom database is defined with `service_name: "wp-db"`, other containers can access it at `wp-db:3306`.
* **External Networks:** You do not need to define or create external Docker networks (e.g. `elastic-net`). All bridge networking is managed automatically by LDM's internal network.
* **Traefik Route Generation:** You can expose your custom container to the host machine via Traefik secure subdomains. Simply define the `"subdomain"` attribute (e.g. `"subdomain": "blog"`). Traefik will automatically generate routing labels and resolve the service at `https://blog.lfr.local` on your host.

## Lifecycle Integration

Once defined, your custom containers act as first-class citizens within the LDM ecosystem.

### Boot Sequence and Port Validation

When running `ldm run`, LDM executes a pre-flight schema validation pass on your custom container definitions. If a required field is malformed, LDM will abort the boot sequence and provide descriptive feedback.

Furthermore, LDM performs preventative **Port Collision Checks** on `127.0.0.1` against any `ports` defined by your custom containers. If a requested port is already in use by another process on your host machine, the boot sequence will gracefully terminate *before* invoking `docker-compose up`, preventing ambiguous `EADDRINUSE` network failures later down the line.

### Dashboard Status checks

Custom containers are automatically labeled and managed by LDM's ComposerService. When you run `ldm status -d`, your custom services will seamlessly populate the visual diagnostics dashboard alongside standard Liferay resources.

## Packaging and Snapshots

When exporting an environment snapshot via `ldm snapshot`, LDM will detect any running custom containers and automatically invoke `docker save` to bundle the active container image binaries directly into your `.ldmp` package or snapshot tarball.

Upon restoration (`ldm import` or environment hydration), LDM invokes `docker load` to reinflate these images. This guarantees that your full multi-compose architecture—Liferay, Postgres, Elasticsearch, and your custom Node.js/WordPress services—are fully portable and completely independent of external Docker Hub network availability during CI/CD test runs.

## Case Study: Packaging a Multi-Service Stack (Rafa's WordPress & Crawler)

The following walkthrough demonstrates how to configure and package a multi-service stack containing a namespaced PostgreSQL database, MySQL, WordPress, and a custom Spring Boot crawler client extension into a single portable `.ldmp` archive.

### 1. Project Scaffolding

Create a new LDM project workspace:

```bash
ldm init ldm-rafa-project
```

### 2. Building Custom Image

Because Rafa's crawler is a custom Spring Boot microservice containing a `Dockerfile`, build it locally on the Docker host:

```bash
docker build -t liferay-cx-crawler:latest ./crawler-src
```

### 3. Project Configuration

Define the custom services in the project's `meta` configuration file using valid JSON format:

```json
{
  "container_name": "ldm-rafa-project",
  "tag": "2025.q1.0-lts",
  "db_type": "postgresql",
  "host_name": "rafa-project.localhost",
  "ssl": true,
  "search_kibana_enabled": true,
  "custom_containers": [
    {
      "service_name": "wp-db",
      "image": "mysql:8.0",
      "environment": [
        "MYSQL_ROOT_PASSWORD=wordpress_root_password",
        "MYSQL_DATABASE=wordpress",
        "MYSQL_USER=wordpress_user",
        "MYSQL_PASSWORD=wordpress_password"
      ],
      "volumes": [
        "wp_db_data:/var/lib/mysql"
      ]
    },
    {
      "service_name": "wordpress",
      "image": "wordpress:latest",
      "depends_on": ["wp-db"],
      "ports": ["8090:80"],
      "environment": [
        "WORDPRESS_DB_HOST=wp-db:3306",
        "WORDPRESS_DB_USER=wordpress_user",
        "WORDPRESS_DB_PASSWORD=wordpress_password",
        "WORDPRESS_DB_NAME=wordpress"
      ],
      "subdomain": "wordpress",
      "volumes": [
        "wp_data:/var/www/html"
      ]
    },
    {
      "service_name": "cx-spring-boot",
      "image": "liferay-cx-crawler:latest",
      "depends_on": ["wordpress"],
      "ports": ["58081:58081"],
      "environment": [
        "COM_LIFERAY_LXC_DXP_DOMAINS=liferay:8080",
        "COM_LIFERAY_LXC_DXP_MAINDOMAIN=liferay:8080",
        "ELASTICSEARCH_HOSTS=http://liferay-search-global:9200"
      ]
    }
  ]
}
```

* **Namespaced Database:** The core database service is compiled as `ldm-rafa-project-db` (namespaced to the project name), ensuring no DNS conflicts occur with other active stacks on the `liferay-net` network.
* **WordPress Subdomain:** Traefik exposes WordPress externally on the host at `https://wordpress.rafa-project.localhost` using the `subdomain` attribute.
* **Internal Networking:** The Spring Boot crawler connects natively to Elasticsearch via `http://liferay-search-global:9200` using LDM's unified bridge network.

### 4. Compiling and Booting the Stack

Compile the Compose configuration and run the environment:

```bash
ldm run -y
```

### 5. Packaging into Standalone `.ldmp`

Export the entire workspace and custom service binaries into a portable package:

```bash
ldm snapshot --export
```

LDM will save the Postgres, MySQL, WordPress, and custom crawler container image binaries, packaging them into the output archive. A developer on another machine can then recreate the exact multi-compose architecture out-of-the-box by running `ldm import`.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-03* | *Last Reviewed: 2026-07-27*
