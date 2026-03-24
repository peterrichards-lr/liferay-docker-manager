#!/bin/zsh

set -o pipefail
SCRIPT_VERSION="2025-11-12"
META_VERSION="2"

export DOCKER_CLI_HINTS=false
Color_Off='\033[0m'
Green='\033[0;32m'
Yellow='\033[0;33m'
White='\033[0;37m'
BYellow='\033[1;33m'
Red='\033[0;31m'
BRed='\033[1;31m'

info() { [[ -n $* && "$QUIET" != 1 ]] && echo -e "${Yellow}$1${Color_Off}"; }
info_custom() { [[ -n $* && "$QUIET" != 1 ]] && echo -e "$1${Color_Off}"; }
error() { echo -e "${BRed}Error:${Color_Off} $*" 1>&2; }
_die() { error "$*"; exit 1; }
read_config() {
  if [[ "$NON_INTERACTIVE" == 1 ]]; then
    typeset -g "$2"="$3"
    return
  fi
  if [[ -n $* ]]; then
    local ANSWER
    echo -n -e "${White}$1 [${Green}$3${White}]: ${Color_Off}"
    read -r ANSWER
    typeset -g "$2"="${ANSWER:-$3}"
  fi
}
read_db_prop() { grep -E "^$1=" "$FILES_VOLUME/portal-ext.properties" | sed -e "s/^$1=//"; }

wait_for_container_stop() {
  local container="$1"
  local timeout=30
  local start=$(date +%s)
  while (( $(date +%s) - start < timeout )); do
    local status=$(docker inspect -f '{{.State.Status}} {{.State.Running}}' "$container" 2>/dev/null)
    [[ -z "$status" ]] && return 0 # No longer exists
    local parts=(${(z)status})
    if [[ "${parts[1]}" == "exited" && "${parts[2]}" == "false" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

LIFERAY_ROOT_ARG=""
SNAPSHOT_NAME_ARG=""
NO_SNAPSHOT_NAME=""
STOP_FLAG=""
NO_STOP_FLAG=""
NON_INTERACTIVE=0
QUIET=0
VERBOSE=0
DB_ONLY=0
FILES_ONLY=0
COMPRESSION="gzip"
PREFIX=""
VERIFY=0
BACKUPS_DIR_OVERRIDE=""
CONTAINER_NAME_OVERRIDE=""
PG_HOST_OVERRIDE=""
PG_PORT_OVERRIDE=""
MY_HOST_OVERRIDE=""
MY_PORT_OVERRIDE=""
RETENTION_N=""
TAGS=()
BACKUP_FORMAT="standard"
FORMAT_SET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -r|--root)
      shift; [[ -z "$1" ]] && _die "--root requires a path"
      LIFERAY_ROOT_ARG="$1" ;;
    -b|--backups-dir)
      shift; [[ -z "$1" ]] && _die "--backups-dir requires a path"
      BACKUPS_DIR_OVERRIDE="$1" ;;
    -c|--container)
      shift; [[ -z "$1" ]] && _die "--container requires a name"
      CONTAINER_NAME_OVERRIDE="$1" ;;
    --non-interactive)
      NON_INTERACTIVE=1 ;;
    --db-only)
      DB_ONLY=1 ;;
    --files-only)
      FILES_ONLY=1 ;;
    --compression)
      shift; [[ -z "$1" ]] && _die "--compression requires a value"
      case "$1" in gzip|xz|none) COMPRESSION="$1" ;; *) _die "--compression must be gzip|xz|none";; esac ;;
    --prefix)
      shift; [[ -z "$1" ]] && _die "--prefix requires a value"
      PREFIX="$1" ;;
    --verify)
      VERIFY=1 ;;
    --quiet)
      QUIET=1 ;;
    --verbose)
      VERBOSE=1 ;;
    --pg-host)
      shift; [[ -z "$1" ]] && _die "--pg-host requires a value"
      PG_HOST_OVERRIDE="$1" ;;
    --pg-port)
      shift; [[ -z "$1" ]] && _die "--pg-port requires a value"
      PG_PORT_OVERRIDE="$1" ;;
    --my-host)
      shift; [[ -z "$1" ]] && _die "--my-host requires a value"
      MY_HOST_OVERRIDE="$1" ;;
    --my-port)
      shift; [[ -z "$1" ]] && _die "--my-port requires a value"
      MY_PORT_OVERRIDE="$1" ;;
    --tag)
      shift; [[ -z "$1" || "$1" != *"="* ]] && _die "--tag requires key=value"
      TAGS+="$1" ;;
    --retention)
      shift; [[ -z "$1" || ! "$1" =~ '^[0-9]+$' ]] && _die "--retention requires an integer"
      RETENTION_N="$1" ;;
    --format)
      shift; [[ -z "$1" ]] && _die "--format requires standard|liferay-cloud"
      case "$1" in
        standard|liferay-cloud) BACKUP_FORMAT="$1"; FORMAT_SET=1 ;;
        *) _die "--format must be standard|liferay-cloud" ;;
      esac ;;
    --cloud)
      BACKUP_FORMAT="liferay-cloud"; FORMAT_SET=1 ;;
    -n|--name)
      shift; [[ -z "$1" ]] && _die "--name requires a value"
      SNAPSHOT_NAME_ARG="$1" ;;
    --no-name)
      NO_SNAPSHOT_NAME=1 ;;
    -s|--stop)
      STOP_FLAG=1 ;;
    --no-stop)
      NO_STOP_FLAG=1 ;;
    --version)
      echo "$0 $SCRIPT_VERSION"; exit 0 ;;
    -h|--help)
      echo "Usage: $0 [options]";
      echo "";
      echo "Core paths and container:";
      echo "  -r, --root <path>             Project root. Default: current directory (interactive prompt if not provided).";
      echo "  -b, --backups-dir <path>      Backups directory. Default: <root>/backups";
      echo "  -c, --container <name>        Override container name. Default: basename(<root>) with dots replaced by dashes";
      echo "";
      echo "Snapshot content and compression:";
      echo "      --db-only / --files-only  Dump only DB or only filesystem. Default: both";
      echo "      --compression gzip|xz|none  Compression for dumps and tar. Default: gzip";
      echo "      --prefix <text>           Prefix for backup folder name (results in <prefix>-<timestamp>)";
      echo "      --format standard|liferay-cloud  Backup layout. Default: standard";
      echo "                                • liferay-cloud: database.gz (plain SQL, no owner/privileges) and volume.tgz (tar.gz of data/document_library)";
      echo "";
      echo "Behavior and prompts:";
      echo "  -s, --stop / --no-stop       Stop container during backup (and restart afterwards if it was running). Default: stop";
      echo "      --non-interactive         Do not prompt; use defaults. In this mode:";
      echo "                                • root defaults to current directory";
      echo "                                • container is stopped by default (unless --no-stop is provided)";
      echo "                                • snapshot name is empty unless --name is provided";
      echo "                                • no prompts are shown (defaults are applied silently)";
      echo "  -n, --name <text>            Optional snapshot name (stored in meta).";
      echo "      --no-name                Skip the name prompt and store no name";
      echo "";
      echo "Database connection overrides (if dumping DB):";
      echo "      --pg-host/--pg-port       Override PostgreSQL host/port. Defaults: parsed from JDBC URL; host.docker.internal -> localhost; port 5432 if missing";
      echo "      --my-host/--my-port       Override MySQL host/port. Defaults: parsed from JDBC URL; host.docker.internal -> localhost; port 3306 if missing";
      echo "";
      echo "Metadata and retention:";
      echo "      --tag key=value           Repeatable; stored in meta as tag.<key>=<value>";
      echo "      --retention <N>           Keep only newest N backups (older ones pruned). If --prefix is set, pruning applies only to backups starting with that prefix";
      echo "";
      echo "Verification and logging:";
      echo "      --verify                  Validate created archives (gzip/xz integrity, tar list)";
      echo "      --quiet | --verbose       Adjust logging verbosity. Default: normal";
      exit 0 ;;
    *) _die "Unknown option: $1" ;;
  esac
  shift
done

if [[ -z "$BACKUP_FORMAT" ]]; then
  BACKUP_FORMAT="standard"
fi
if [[ "$NON_INTERACTIVE" -ne 1 && "$FORMAT_SET" -ne 1 ]]; then
  read_config "Backup format (standard|liferay-cloud)" BACKUP_FORMAT "${BACKUP_FORMAT}"
  case "$BACKUP_FORMAT" in
    standard|liferay-cloud) : ;;
    *) _die "Invalid format: $BACKUP_FORMAT (expected: standard or liferay-cloud)" ;;
  esac
fi

[[ $VERBOSE -eq 1 ]] && set -x

if [[ $DB_ONLY -eq 1 && $FILES_ONLY -eq 1 ]]; then
  _die "--db-only and --files-only are mutually exclusive"
fi

if [[ -n "$LIFERAY_ROOT_ARG" ]]; then
  LIFERAY_ROOT="$LIFERAY_ROOT_ARG"
else
  # Smart detection
  if [[ -d "files" || -d "deploy" || -f ".liferay-docker.meta" ]]; then
    LIFERAY_ROOT="$(pwd)"
  elif [[ "$NON_INTERACTIVE" -ne 1 ]]; then
    folders=()
    for d in */; do
      d=${d%/}
      if [[ -d "$d/files" || -d "$d/deploy" || -f "$d/.liferay-docker.meta" ]]; then
        [[ "$d" != "common" && "$d" != .* ]] && folders+=("$d")
      fi
    done
    
    if (( ${#folders[@]} > 0 )); then
      info_custom "${BYellow}=== Select Managed Folder for Snapshot ==="
      for i in {1..${#folders[@]}}; do
        info_custom "  [$i] ${folders[$i]}"
      done
      read_config "Select folder index" CHOICE_IDX 1
      if [[ "$CHOICE_IDX" -gt 0 && "$CHOICE_IDX" -le ${#folders[@]} ]]; then
        LIFERAY_ROOT="${folders[$CHOICE_IDX]}"
      fi
    fi
  fi
  
  if [[ -z "$LIFERAY_ROOT" ]]; then
    read_config "Liferay Root path" LIFERAY_ROOT ""
    [[ -z "$LIFERAY_ROOT" ]] && _die "Liferay Root is required."
  fi
fi
[[ ! "$LIFERAY_ROOT" =~ ^(\.\/|\/).+$ ]] && LIFERAY_ROOT="./$LIFERAY_ROOT"
# Normalize path to absolute
LIFERAY_ROOT=$(cd "$LIFERAY_ROOT" 2>/dev/null && pwd || echo "$LIFERAY_ROOT")

DEPLOY_VOLUME="$LIFERAY_ROOT/deploy"
DATA_VOLUME="$LIFERAY_ROOT/data"
SCRIPT_VOLUME="$LIFERAY_ROOT/scripts"
FILES_VOLUME="$LIFERAY_ROOT/files"
CX_VOLUME="$LIFERAY_ROOT/osgi/client-extensions"
STATE_VOLUME="$LIFERAY_ROOT/osgi/state"
MODULES_VOLUME="$LIFERAY_ROOT/osgi/modules"
BACKUPS_DIR="${BACKUPS_DIR_OVERRIDE:-$LIFERAY_ROOT/backups}"

mkdir -p "$BACKUPS_DIR"

if [[ -n "$CONTAINER_NAME_OVERRIDE" ]]; then
  CONTAINER_NAME="$CONTAINER_NAME_OVERRIDE"
else
  CONTAINER_NAME=$(echo "$LIFERAY_ROOT" | sed -e 's:.*/::' -e 's/[\.]/-/g')
fi
container_running=$(docker ps --format '{{.Names}}' | grep -x "$CONTAINER_NAME" >/dev/null 2>&1 && echo "Y" || echo "N")

STOP_CONTAINER_DEFAULT=Y
if [[ -n "$STOP_FLAG" ]]; then
  STOP_CONTAINER=Y
elif [[ -n "$NO_STOP_FLAG" ]]; then
  STOP_CONTAINER=N
else
  read_config "Stop container during backup" STOP_CONTAINER "$STOP_CONTAINER_DEFAULT"
fi

if [[ "${STOP_CONTAINER:u}" == "Y" && "$container_running" == "Y" ]]; then
  info_custom "${Yellow}Stopping ${Green}$CONTAINER_NAME"
  docker stop "$CONTAINER_NAME" >/dev/null 2>&1
  ! wait_for_container_stop "$CONTAINER_NAME" && _die "Container failed to stop within timeout."
  # Add a safety buffer for external drive I/O latency (e.g. SanDisk/USB)
  sleep 3
fi

timestamp=$(date +"%Y%m%d-%H%M%S")
dir_name="$timestamp"
[[ -n "$PREFIX" ]] && dir_name="$PREFIX-$dir_name"
checkpoint_dir="$BACKUPS_DIR/$dir_name"
mkdir -p "$checkpoint_dir"

_db_meta_value=""
_files_meta_value=""

if [[ -n "$SNAPSHOT_NAME_ARG" ]]; then
  SNAPSHOT_NAME="$SNAPSHOT_NAME_ARG"
elif [[ "$NO_SNAPSHOT_NAME" == 1 || "$NON_INTERACTIVE" == 1 ]]; then
  SNAPSHOT_NAME=""
else
  read_config "Snapshot Name (optional)" SNAPSHOT_NAME ""
fi

case "$COMPRESSION" in
  gzip) TAR_FLAG=z; COMPRESS_EXT=".gz"; COMPRESS_CMD="gzip -c" ;;
  xz)   TAR_FLAG=J; COMPRESS_EXT=".xz"; COMPRESS_CMD="xz -c" ;;
  none) TAR_FLAG=""; COMPRESS_EXT="";  COMPRESS_CMD="cat" ;;
esac

jdbc_url=""; jdbc_user=""; jdbc_pass=""
if [[ -f "$FILES_VOLUME/portal-ext.properties" ]]; then
  jdbc_url=$(read_db_prop "jdbc.default.url")
  jdbc_user=$(read_db_prop "jdbc.default.username")
  jdbc_pass=$(read_db_prop "jdbc.default.password")
fi

if [[ -z "$jdbc_url" ]]; then
  snap_type="hypersonic"
else
  echo "$jdbc_url" | grep -qi "postgresql" && snap_type="postgresql"
  echo "$jdbc_url" | grep -qi "mysql" && snap_type="${snap_type:-mysql}"
fi

if [[ "$BACKUP_FORMAT" == "liferay-cloud" && "$snap_type" == "hypersonic" ]]; then
  _die "Liferay Cloud format requires PostgreSQL or MySQL; Hypersonic is not supported"
fi

{
  printf "meta_version=%s\n" "$META_VERSION"
  printf "type=%s\n" "$snap_type"
  printf "format=%s\n" "$BACKUP_FORMAT"
  [[ -n "$SNAPSHOT_NAME" ]] && printf "name=%s\n" "$SNAPSHOT_NAME"
  for tag in "${TAGS[@]}"; do
    key="${tag%%=*}"; val="${tag#*=}"
    printf "tag.%s=%s\n" "$key" "$val"
  done
} > "$checkpoint_dir/meta"

if [[ $FILES_ONLY -eq 0 ]]; then
  if [[ "$snap_type" == "postgresql" ]]; then
    dbname=$(echo "$jdbc_url" | sed -E 's#^jdbc:postgresql://[^/]+/([^?]+).*#\1#')
    pghost="${PG_HOST_OVERRIDE:-localhost}"
    pgport="${PG_PORT_OVERRIDE:-5432}"
    [[ "$pghost" == "host.docker.internal" ]] && pghost="localhost"

    # Pre-flight Check: Verify connectivity and credentials
    info "Verifying PostgreSQL connectivity & auth ($pghost:$pgport)..."
    PGPASSWORD="$jdbc_pass" psql -h "$pghost" -p "$pgport" -U "$jdbc_user" -d postgres -c "SELECT 1" >/dev/null 2>&1
    if [[ $? -ne 0 ]]; then
      _die "PostgreSQL database is not reachable or authentication failed on $pghost:$pgport."
    fi

    if [[ "$BACKUP_FORMAT" == "liferay-cloud" ]]; then
      dump_file="$checkpoint_dir/database.gz"; dump_cmd="pg_dump -h $pghost -p $pgport -U $jdbc_user -d $dbname -F p --no-owner --no-privileges | gzip -c"
    else
      dump_file="$checkpoint_dir/db-postgresql.sql$COMPRESS_EXT"; dump_cmd="pg_dump -h $pghost -p $pgport -U $jdbc_user -d $dbname | eval $COMPRESS_CMD"
    fi
    info "Dumping PostgreSQL database: $dbname"
    PGPASSWORD="$jdbc_pass" eval "$dump_cmd" > "$dump_file"
    if [[ $? -ne 0 || ! -s "$dump_file" ]]; then rm -f "$dump_file"; _die "Database dump failed (PostgreSQL)."; fi
    _db_meta_value="$(basename "$dump_file")"
  elif [[ "$snap_type" == "mysql" ]]; then
    dbname=$(echo "$jdbc_url" | sed -E 's#^jdbc:mysql://[^/]+/([^?]+).*#\1#')
    myhost="${MY_HOST_OVERRIDE:-localhost}"
    myport="${MY_PORT_OVERRIDE:-3306}"
    [[ "$myhost" == "host.docker.internal" ]] && myhost="localhost"

    # Pre-flight Check: Verify connectivity and credentials
    info "Verifying MySQL connectivity & auth ($myhost:$myport)..."
    mysql -h "$myhost" -P "$myport" -u "$jdbc_user" -p"$jdbc_pass" -e "SELECT 1" >/dev/null 2>&1
    if [[ $? -ne 0 ]]; then
      _die "MySQL database is not reachable or authentication failed on $myhost:$myport."
    fi

    if [[ "$BACKUP_FORMAT" == "liferay-cloud" ]]; then
      dump_file="$checkpoint_dir/database.gz"; dump_cmd="mysqldump -h $myhost -P $myport -u $jdbc_user -p$jdbc_pass --databases $dbname | gzip -c"
    else
      dump_file="$checkpoint_dir/db-mysql.sql$COMPRESS_EXT"; dump_cmd="mysqldump -h $myhost -P $myport -u $jdbc_user -p$jdbc_pass --databases $dbname | eval $COMPRESS_CMD"
    fi
    info "Dumping MySQL database: $dbname"
    eval "$dump_cmd" > "$dump_file"
    if [[ $? -ne 0 || ! -s "$dump_file" ]]; then rm -f "$dump_file"; _die "Database dump failed (MySQL)."; fi
    _db_meta_value="$(basename "$dump_file")"
  fi
fi

if [[ $DB_ONLY -eq 0 ]]; then
  # Exclude generated/volatile directories
  EXCLUDES=("--exclude=osgi/state" "--exclude=data/elasticsearch7" "--exclude=data/elasticsearch" "--exclude=*.sock" "--exclude=*.lock")
  if [[ "$BACKUP_FORMAT" == "liferay-cloud" ]]; then
    src_dir="$LIFERAY_ROOT/data/document_library"
    archive_file="$checkpoint_dir/volume.tgz"
    if [[ -d "$src_dir" ]]; then
      info "Capturing document_library..."
      # Use the normalized absolute LIFERAY_ROOT to ensure consistency under sudo
      tar "${EXCLUDES[@]}" -czf "$archive_file" -C "$LIFERAY_ROOT/data" document_library
      [[ $? -ne 0 ]] && _die "Filesystem archival failed."
      _files_meta_value="$(basename "$archive_file")"
    fi
  else
    info "Archiving Liferay volumes..."
    files_archive="$checkpoint_dir/files.tar${COMPRESS_EXT}"
    # Use the normalized absolute LIFERAY_ROOT to ensure consistency under sudo
    tar "${EXCLUDES[@]}" -c${TAR_FLAG:-}f "$files_archive" -C "$LIFERAY_ROOT" files scripts osgi data deploy modules
    [[ $? -ne 0 ]] && _die "Filesystem archival failed."
    _files_meta_value="$(basename "$files_archive")"
  fi
fi

if [[ $VERIFY -eq 1 ]]; then
  info "Verifying snapshot integrity..."
  [[ -n "$_db_meta_value" ]] && { case "$_db_meta_value" in *.gz) gzip -t "$checkpoint_dir/$_db_meta_value" ;; *.xz) xz -t "$checkpoint_dir/$_db_meta_value" ;; esac || _die "DB dump verification failed."; }
  [[ -n "$_files_meta_value" ]] && { tar -tf "$checkpoint_dir/$_files_meta_value" >/dev/null || _die "Files archive verification failed."; }
fi

{
  [[ -n "$_db_meta_value" ]] && printf "db_dump=%s\n" "$_db_meta_value"
  [[ -n "$_files_meta_value" ]] && printf "files_archive=%s\n" "$_files_meta_value"
} >> "$checkpoint_dir/meta"

if [[ "${STOP_CONTAINER:u}" == "Y" && "$container_running" == "Y" ]]; then
  info_custom "${Yellow}Starting ${Green}$CONTAINER_NAME"
  docker start "$CONTAINER_NAME" >/dev/null 2>&1
fi

if [[ -n "$RETENTION_N" ]]; then
  info "Pruning backups to keep newest $RETENTION_N"
  if [[ -d "$BACKUPS_DIR" ]]; then
    IFS=$'\n' all_bks=($(ls -1 "$BACKUPS_DIR" 2>/dev/null | sort -r))
    unset IFS
    kept=0
    for d in "${all_bks[@]}"; do
      if [[ -n "$PREFIX" && "$d" != "$PREFIX"-* ]]; then
        continue
      fi
      kept=$((kept+1))
      if (( kept > RETENTION_N )); then
        rm -rf "$BACKUPS_DIR/$d"
      fi
    done
  fi
fi

info_custom "${Green}Backup created:${Color_Off} $checkpoint_dir ${SNAPSHOT_NAME:+($SNAPSHOT_NAME)}"
echo -e "\n${White}Notice a bug or have a feature request? Please report it on GitHub at:"
echo -e "${Cyan}https://github.com/peterrichards-lr/liferay-docker-scripts${Color_Off}"