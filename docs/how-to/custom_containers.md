# Managing Custom Containers ![Added in v2.15.19](https://img.shields.io/badge/Added%20in-v2.15.19-blue)

Liferay Docker Manager (LDM) natively supports injecting user-defined custom containers (e.g., MySQL, WordPress, Node.js frontends, or Elasticsearch Web Crawlers) directly into your project's `docker-compose.yml` lifecycle.

By defining custom containers in your project configurations, you can ensure your external services spin up and tear down in perfect sync with your core Liferay infrastructure, and benefit from LDM's garbage collection, collision checks, and automated snapshot packaging.

## Defining Custom Containers

Custom containers can be defined globally in your `~/.ldmrc` or at the project level in `[workspace]/project.json`.

Project-level definitions will always override global definitions.

### Configuration Schema

Under the `custom_containers` block, define your services using a dictionary of standard Docker Compose attributes:

```json
{
  "custom_containers": {
    "my-frontend": {
      "image": "node:18-alpine",
      "command": "npm run dev",
      "ports": ["3000:3000"],
      "environment": {
        "NODE_ENV": "development",
        "API_URL": "http://liferay:8080"
      },
      "volumes": ["./frontend:/app"],
      "working_dir": "/app"
    },
    "wordpress": {
      "image": "wordpress:latest",
      "ports": ["8000:80"],
      "environment": {
        "WORDPRESS_DB_HOST": "db:5432"
      }
    }
  }
}
```

> [!TIP]
> Custom containers are automatically connected to the shared `liferay-net` Docker network. This allows your custom services to reference `liferay`, `db`, and `search` directly via their internal DNS hostnames.

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

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-20* | *Last Reviewed: 2026-07-16*
