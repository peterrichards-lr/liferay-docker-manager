# Multi-Compose Architecture Integration ![Added in v2.15.20](https://img.shields.io/badge/Added%20in-v2.15.20-blue)

While Liferay Docker Manager (LDM) typically manages a single, unified `docker-compose.yml` for your project (including databases and sidecar search), some enterprise architectures require **decoupled environments**.

This guide explains how to set up a **Multi-Compose Architecture**, separating Liferay, Elasticsearch, and custom containers (like a WordPress LAMP stack with Elastic Web Crawler) into isolated, independently manageable environments that communicate over shared external Docker networks.

## Architecture Overview

The multi-compose setup relies on three decoupled compose environments and two shared external networks:

1. **Liferay Compose**: Runs Liferay and PostgreSQL. Connects to the private `liferay-internal` network and the external `shared-search-net`.
2. **Elasticsearch Compose**: Runs Elasticsearch and (optionally) Kibana. Connects to the private `elastic-internal` network and the external `shared-search-net`.
3. **WordPress & Web Crawler Compose**: Runs WordPress, MariaDB, and the Elastic Web Crawler. Connects to the private `wordpress-internal` network, the external `shared-crawl-net`, and the external `shared-search-net`.

This segmentation prevents security cross-contamination (e.g., WordPress has no direct network access to PostgreSQL or Liferay) while allowing the Crawler to bridge the gap between WordPress and Elasticsearch.

## 1. Create the Shared Networks

Before starting any environments, you must manually create the external Docker networks that will bridge them:

```bash
docker network create shared-search-net
docker network create shared-crawl-net
```

## 2. Deploy the Environments

Reference templates for these environments are located in the `docker-compose-templates/` directory of the LDM repository. You can use these templates to build your isolated stacks.

### Step 2.1: Start Elasticsearch

Navigate to your Elasticsearch compose directory (e.g., `docker-compose-templates/elasticsearch/`) and start the stack:

```bash
cd docker-compose-templates/elasticsearch
docker-compose up -d
```

> [!NOTE]
> To include the Kibana interface, use the profile flag: `docker-compose --profile kibana up -d`

### Step 2.2: Start Liferay

Navigate to your Liferay compose directory and start the stack. Liferay is configured to look for the `elasticsearch` host on the `shared-search-net`.

```bash
cd ../liferay
docker-compose up -d
```

### Step 2.3: Start WordPress and the Web Crawler

Finally, start the WordPress environment. The Elastic Web Crawler container in this stack connects to both `shared-crawl-net` (to crawl WordPress) and `shared-search-net` (to push data to Elasticsearch).

```bash
cd ../wordpress
docker-compose up -d
```

## Validating the Setup

Once all three environments are running, you can validate the connections:

1. **Liferay** should successfully index its documents into the standalone Elasticsearch instance.
2. The **Elastic Web Crawler** can be configured (via Kibana or the Elasticsearch API) to crawl `http://wordpress:80` and store the parsed content into an Elasticsearch index.
3. Liferay can then be configured to search the newly created WordPress index, creating a unified search experience across both platforms.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-20* | *Last Reviewed: 2026-07-17*
