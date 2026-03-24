# Liferay Docker Manager (LDM) Architecture

This document contains visual diagrams of the LDM environment, volume structure, and routing logic. Use a Mermaid-compatible viewer (like VS Code's Markdown Preview) to see the graphics.

## 1. Environment Architecture

This diagram illustrates how the `ldm` tool orchestrates the main Liferay instance, the shared infrastructure, and the client extensions.

```mermaid
graph TD
    subgraph Host_Machine [Host Machine]
        CLI[ldm CLI / Python Package]
        FS[(Local File System)]
        Socket[Docker Socket / var/run/docker.sock]
        Cert[mkcert / SSL Store]
    end

    subgraph Docker_Network [Shared Network: liferay-net]
        Proxy[Traefik Proxy: liferay-proxy-global]
        Socat[Socat Bridge: Optional Fallback for macOS]
        Search[Elasticsearch: liferay-search-global]
        
        subgraph Project_Stack [Project Stack]
            DXP[Liferay DXP Container]
            SSCE1[SSCE: Custom Elements]
            SSCE2[SSCE: Node etc]
            SERVICE[Standalone Service: e.g. jBPM]
        end
    end

    %% Traffic Flow
    User((Browser)) -- "https://forge.local" --> Proxy
    User -- "https://ext.forge.local" --> Proxy
    User -- "https://jbpm.forge.local" --> Proxy
    
    Proxy -- "Namespaced Router routing" --> DXP
    Proxy -- "Namespaced Router routing" --> SSCE1
    Proxy -- "Namespaced Router routing" --> SSCE2
    Proxy -- "Namespaced Router routing" --> SERVICE
    
    %% Communication
    DXP -- "Namespaced Indexing" --> Search
    Proxy -- "API Events" --> Socket
    Socat -.-> Socket
    
    %% Persistence
    DXP -- "Volumes" --> FS
    SSCE1 -- "Metadata Path" --> FS
    
    %% Config
    Cert -.-> Proxy
    CLI -- "Orchestrates" --> Docker_Network
```

---

## 2. Volume Mounting Structure

This diagram shows how `ldm` maps your local project folder into the containers to allow for hot-reloading and data persistence.

```mermaid
graph LR
    subgraph Host_Project_Dir [Host: /project-name/]
        direction TB
        H_Files[files/]
        H_Deploy[deploy/]
        H_OSGi[osgi/]
        H_Data[data/]
        H_Routes[routes/]
        H_Logs[logs/]
    end

    subgraph Liferay_Container [Liferay DXP Container]
        direction TB
        C_Files["/mnt/liferay/files"]
        C_Deploy["/mnt/liferay/deploy"]
        C_Configs["/opt/liferay/osgi/configs"]
        C_Modules["/opt/liferay/osgi/modules"]
        C_CX["/opt/liferay/osgi/client-extensions"]
        C_Data["/opt/liferay/data"]
        C_Routes["/opt/liferay/routes"]
        C_Logs["/opt/liferay/logs"]
    end

    subgraph SSCE_Container [Client Extension Container]
        S_Routes["/opt/liferay/routes"]
    end

    %% Mappings
    H_Files --- C_Files
    H_Deploy --- C_Deploy
    H_OSGi --- C_Configs
    H_OSGi --- C_Modules
    H_OSGi --- C_CX
    H_Data --- C_Data
    H_Routes --- C_Routes
    H_Logs --- C_Logs
    
    %% Shared Metadata
    H_Routes --- S_Routes
```

---

## 3. Client Extension Deployment Lifecycle

This diagram illustrates the dual path `ldm` takes when it finds a Client Extension zip: building the Docker service and providing the OSGi configuration to Liferay.

```mermaid
graph TD
    subgraph Host_FS [Host File System]
        Zip[CX Zip File]
        CX_Build[client-extensions/extension-id/]
        OSGi_CX[osgi/client-extensions/extension-id.zip]
        Metadata[routes/default/extension-id/]
    end

    subgraph LDM_Logic [ldm scan_client_extensions]
        Detect{Has Dockerfile?}
        Extract[Unzip context to CX_Build]
        Move[Move zip to OSGi_CX]
    end

    subgraph Docker_Engine [Docker Engine]
        Build[docker build]
        Run[Container Service]
    end

    subgraph Liferay_Runtime [Liferay DXP]
        OSGi[OSGi Scanner]
        Discovery[Metadata Scanner]
    end

    %% Lifecycle Flow
    Zip --> Detect
    Detect -- "Yes (SSCE)" --> Extract
    Detect -- "Always" --> Move
    
    Extract --> Build
    Build --> Run
    
    Move --> OSGi
    OSGi -- "Registers ID & OAuth" --> Discovery
    
    Run -- "Writes Endpoint info" --> Metadata
    Metadata -- "Detects URL" --> Discovery
```

---

## 4. Subdomain Routing Logic

This diagram illustrates how a single Traefik instance uses the `Host` header and Docker labels to route encrypted traffic to the correct service.

```mermaid
sequenceDiagram
    participant U as User Browser
    participant T as Traefik (Proxy Global)
    participant L as Liferay Container
    participant E as SSCE Container (Node/JS)

    Note over U, T: All traffic via Port 443 (SSL)
    
    U->>T: GET https://forge.local
    Note right of T: Rule: Host(`forge.local`)
    T->>L: Route to port 8080 (Namespaced Service)
    L-->>U: HTTP 200 (Liferay Home)

    U->>T: GET https://my-ext.forge.local
    Note right of T: Rule: Host(`my-ext.forge.local`)
    T->>E: Route to port 8080 (Extension Service)
    E-->>U: HTTP 200 (Extension Resource)

    Note over T, E: Labels generated by ldm:<br/>traefik.http.routers.[project]-main.rule=Host(...)<br/>traefik.http.routers.[project]-main.tls=true
```

## 5. Workspace Import Engine

This diagram shows how `ldm import` transforms a Liferay Workspace (Standard or Cloud) into an `ldm` project.

```mermaid
graph TD
    Source[Source Workspace Path] --> Detect{Detect Type}
    
    Detect -- "Standard (gradle.properties)" --> StdRoot[Workspace Root]
    Detect -- "Cloud (liferay/LCP.json)" --> CloudRoot[liferay/ Root]
    
    StdRoot --> Metadata[Extract Tag from gradle.properties]
    CloudRoot --> Metadata
    CloudRoot --> Limits[Extract CPU/Memory from LCP.json]
    
    Metadata --> Scaffold[Create ldm Project Structure]
    Limits --> Scaffold
    
    StdRoot --> Assets[Scan for Built Artifacts]
    CloudRoot --> Assets
    
    Assets --> CX[Client Extensions dist/*.zip]
    Assets --> FRAG[Fragments dist/*.zip]
    
    FRAG --> Validate{Has liferay-deploy-fragments.json?}
    Validate -- "Yes" --> CopyCX[Copy to ce_dir/]
    Validate -- "No" --> Reject[Report Invalid Fragment]
    CX --> CopyCX
    
    StdRoot --> Config[Import configs/env/ settings]
    CloudRoot --> Config
    
    subgraph Cloud_Only [Cloud Specific]
        CloudSource[Original Source Path] --> ScanServices[Scan for Standalone Services]
        ScanServices -- "Has LCP.json + Dockerfile" --> CopyService[Copy to services/]
    end
```

### Key Architectural Pillars

1. **Modular Orchestration (ldm_core Package):**
    * The tool logic is split into specialized handler mixins (`Stack`, `Workspace`, `Config`, `Snapshot`, `Diagnostics`), ensuring a maintainable and extensible codebase.
    * Every command supports a standardized discovery priority: **Argument > Flag > CWD > Interactive Selection**.

2. **Shared Infrastructure (Global Tier):**
    * **Traefik (`liferay-proxy-global`)**: A singleton container that handles all SSL termination and namespaced routing. It works natively on **Linux, WSL2, and Colima** by detecting the standard Docker socket.
    * **Elasticsearch (`liferay-search-global`)**: A shared ES8 instance that uses project-specific index prefixes, allowing multiple projects to share one search cluster efficiently.
    * **Socat Bridge (Fallback)**: An optional bridge used only on macOS when the standard `/var/run/docker.sock` is missing (primarily for Docker Desktop isolation).

3. **Multi-Instance Isolation (Project Tier):**
    * **Network Stability**: All services use unique namespacing for Traefik routers and services (e.g., `[project-id]-main`), preventing routing collisions.
    * **Session Security**: Unique session cookie names are generated based on the project's virtual hostname to prevent session cross-talk.
    * **Standalone Services**: Arbitrary containers (like jBPM) placed in the `services/` folder are seamlessly orchestrated with the same routing and resource guardrails as Liferay.

4. **Persistence & State Management:**
    * **Orchestrated Snapshots**: Project snapshots include the database, Document Library, and the **Elasticsearch 8.x index state**, ensuring consistent recovery.
    * **Automated Healthchecks**: Converts `LCP.json` probes into native Docker healthchecks for robust orchestration.
    * **SSL**: `mkcert` provides automated, locally trusted wildcard certificates for all project subdomains.

### 5. Resource Identification & Metadata

To ensure reliable global maintenance and pruning, LDM injects specialized Docker labels into every container it creates:

| Label | Purpose | Example |
| :--- | :--- | :--- |
| `com.liferay.ldm.managed` | Flags the container as LDM-controlled. | `true` |
| `com.liferay.ldm.project` | Identifies which LDM project the container belongs to. | `my-project` |

The `ldm prune` command uses these labels to cross-reference active containers against the projects present on the filesystem, allowing it to safely identify and remove orphans from deleted projects.
