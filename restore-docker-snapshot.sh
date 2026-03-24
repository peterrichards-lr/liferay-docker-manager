#!/bin/zsh

set -o pipefail

SCRIPT_VERSION="2025-11-12"
MIN_META_VERSION=2
ALLOW_LEGACY=0

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

safe_extract() {
  local archive="$1"; local target="$2"
  # Zip Slip protection: Check if archive contains paths that escape the target
  if tar -tf "$archive" | grep -q "\.\./"; then
    _die "Security Alert: Path traversal detected in archive: $archive"
  fi
  
  case "$archive" in
    *.gz) tar -xzf "$archive" -C "$target" ;;
    *.xz) tar -xJf "$archive" -C "$target" ;;
    *)    tar -xf  "$archive" -C "$target" ;;
  esac
}

LIFERAY_ROOT_ARG=""
BACKUPS_DIR_OVERRIDE=""
CONTAINER_NAME_OVERRIDE=""
NON_INTERACTIVE=0
QUIET=0
VERBOSE=0
INDEX_ARG=""
CHECKPOINT_ARG=""
NO_LIST=0
STOP_FLAG=""
NO_STOP_FLAG=""
DELETE_STATE_FLAG=""
DELETE_ES_FLAG=""
PG_HOST_OVERRIDE=""
PG_PORT_OVERRIDE=""
MY_HOST_OVERRIDE=""
MY_PORT_OVERRIDE=""
DELETE_AFTER_FLAG=""
KEEP_CHECKPOINT_FLAG=""
FORMAT_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -r|--root)
      shift; [[ -z "$1" ]] && _die "--root requires a path"; LIFERAY_ROOT_ARG="$1" ;;
    -b|--backups-dir)
      shift; [[ -z "$1" ]] && _die "--backups-dir requires a path"; BACKUPS_DIR_OVERRIDE="$1" ;;
    -c|--container)
      shift; [[ -z "$1" ]] && _die "--container requires a name"; CONTAINER_NAME_OVERRIDE="$1" ;;
    --non-interactive)
      NON_INTERACTIVE=1 ;;
    -i|--index)
      shift; [[ -z "$1" || ! "$1" =~ '^[0-9]+$' ]] && _die "--index requires a numeric value"; INDEX_ARG="$1" ;;
    --checkpoint)
      shift; [[ -z "$1" ]] && _die "--checkpoint requires a folder name"; CHECKPOINT_ARG="$1" ;;
    --no-list)
      NO_LIST=1 ;;
    -s|--stop)
      STOP_FLAG=1 ;;
    --no-stop)
      NO_STOP_FLAG=1 ;;
    --delete-state)
      DELETE_STATE_FLAG=1 ;;
    --delete-elasticsearch)
      DELETE_ES_FLAG=1 ;;
    --pg-host)
      shift; [[ -z "$1" ]] && _die "--pg-host requires a value"; PG_HOST_OVERRIDE="$1" ;;
    --pg-port)
      shift; [[ -z "$1" ]] && _die "--pg-port requires a value"; PG_PORT_OVERRIDE="$1" ;;
    --my-host)
      shift; [[ -z "$1" ]] && _die "--my-host requires a value"; MY_HOST_OVERRIDE="$1" ;;
    --my-port)
      shift; [[ -z "$1" ]] && _die "--my-port requires a value"; MY_PORT_OVERRIDE="$1" ;;
    --delete-after)
      DELETE_AFTER_FLAG=1 ;;
    --keep-checkpoint)
      KEEP_CHECKPOINT_FLAG=1 ;;
    --format)
      shift; [[ -z "$1" ]] && _die "--format requires standard|liferay-cloud"; case "$1" in standard|liferay-cloud) FORMAT_OVERRIDE="$1" ;; *) _die "--format must be standard|liferay-cloud";; esac ;;
    --min-meta-version)
      shift; [[ -z "$1" || "$1" != <-> ]] && _die "--min-meta-version requires an integer"; MIN_META_VERSION="$1" ;;
    --allow-legacy)
      ALLOW_LEGACY=1 ;;
    --quiet)
      QUIET=1 ;;
    --verbose)
      VERBOSE=1 ;;
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
      echo "Selection of backup to restore (newest-first order):";
      echo "  -i, --index <N>               Select backup by numeric index (1 = newest)";
      echo "      --checkpoint <folder>     Select backup by exact folder name";
      echo "      --no-list                 Suppress listing of backups (listing is auto-suppressed in non-interactive mode)";
      echo "";
      echo "Runtime behavior:";
      echo "  -s, --stop / --no-stop       Stop container during restore (and restart afterwards if it was running). Default: stop";
      echo "      --delete-after            Delete checkpoint after successful restore";
      echo "      --keep-checkpoint         Keep checkpoint after restore. Default: keep";
      echo "      --delete-state            Delete OSGi state folder before restore";
      echo "      --delete-elasticsearch    Delete Elasticsearch data folder (data/elasticsearch7) before restore";
      echo "      --non-interactive         Do not prompt; use defaults. In this mode:";
      echo "                                • root defaults to current directory";
      echo "                                • backup selection defaults to newest backup unless --index/--checkpoint is provided";
      echo "                                • container is stopped by default (unless --no-stop is provided)";
      echo "                                • checkpoint is kept by default (unless --delete-after is provided)";
      echo "                                • backup list is not shown";
      echo "      --format <standard|liferay-cloud>  Override auto-detected backup layout. Default: auto-detect";
      echo "";
      echo "      --min-meta-version <N>    Require meta_version >= N in backup meta (default: 2)";
      echo "      --allow-legacy            Allow restoring backups with meta_version < min supported";
      echo "";
      echo "Database connection overrides (if restoring DB):";
      echo "      --pg-host/--pg-port       Override PostgreSQL host/port. Defaults: parsed from JDBC URL; host.docker.internal -> localhost; port 5432 if missing";
      echo "      --my-host/--my-port       Override MySQL host/port. Defaults: parsed from JDBC URL; host.docker.internal -> localhost; port 3306 if missing";
      echo "";
      echo "Logging:";
      echo "      --quiet | --verbose       Adjust logging verbosity. Default: normal";
      exit 0 ;;
    *) _die "Unknown option: $1" ;;
  esac
  shift
done

if [[ "$NON_INTERACTIVE" -eq 1 && -n "$DELETE_STATE_FLAG$DELETE_ES_FLAG" && -z "$STOP_FLAG" ]]; then
  _die "--delete-state/--delete-elasticsearch require -s or --stop in non-interactive mode"
fi

[[ $VERBOSE -eq 1 ]] && set -x

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
      info_custom "${BYellow}=== Select Managed Folder for Restore ==="
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
# Restrict to root
LIFERAY_ROOT=$(realpath "$LIFERAY_ROOT")

DEPLOY_VOLUME="$LIFERAY_ROOT/deploy"
DATA_VOLUME="$LIFERAY_ROOT/data"
SCRIPT_VOLUME="$LIFERAY_ROOT/scripts"
FILES_VOLUME="$LIFERAY_ROOT/files"
CX_VOLUME="$LIFERAY_ROOT/osgi/client-extensions"
STATE_VOLUME="$LIFERAY_ROOT/osgi/state"
MODULES_VOLUME="$LIFERAY_ROOT/osgi/modules"
BACKUPS_DIR="${BACKUPS_DIR_OVERRIDE:-$LIFERAY_ROOT/backups}"

CHECKPOINTS=()
if [[ -d "$BACKUPS_DIR" ]]; then
  IFS=$'\n' CHECKPOINTS=($(ls -1 "$BACKUPS_DIR" 2>/dev/null | sort -r))
  unset IFS
fi
latest_checkpoint="${CHECKPOINTS[1]}"

if [[ ${#CHECKPOINTS[@]} -eq 0 ]]; then
  info_custom "${Yellow}No backups found in:${Color_Off} $BACKUPS_DIR"
  exit 1
fi

if [[ -n "$CHECKPOINT_ARG" ]]; then
  CHECKPOINT="$CHECKPOINT_ARG"
elif [[ -n "$INDEX_ARG" ]]; then
  sel=$((INDEX_ARG))
  if (( sel < 1 || sel > ${#CHECKPOINTS[@]} )); then
    _die "Invalid --index: $INDEX_ARG"
  fi
  CHECKPOINT="${CHECKPOINTS[$sel]}"
else
  if [[ $NO_LIST -eq 0 ]]; then
    info_custom "${Yellow}Available backups for${Color_Off} $BACKUPS_DIR"
    idx=1
    for folder in "${CHECKPOINTS[@]}"; do
      name_line=$(sed -n 's/^name=//p' "$BACKUPS_DIR/$folder/meta" 2>/dev/null | head -n1)
      display_name=${name_line:-"(unnamed)"}
      echo "  [$idx] $display_name — $folder"
      idx=$((idx+1))
    done
  fi
  read_config "Select backup by number or enter folder name" CHECKPOINT_INPUT "$latest_checkpoint"
  if [[ "$CHECKPOINT_INPUT" =~ ^[0-9]+$ ]]; then
    sel=$((CHECKPOINT_INPUT))
    if (( sel < 1 || sel > ${#CHECKPOINTS[@]} )); then
      _die "Invalid selection: $CHECKPOINT_INPUT"
    fi
    CHECKPOINT="${CHECKPOINTS[$sel]}"
  else
    CHECKPOINT="$CHECKPOINT_INPUT"
  fi
fi

CHECKPOINT_DIR="$BACKUPS_DIR/$CHECKPOINT"
[[ ! -d "$CHECKPOINT_DIR" ]] && _die "Checkpoint not found: $CHECKPOINT_DIR"

if [[ -n "$CONTAINER_NAME_OVERRIDE" ]]; then
  CONTAINER_NAME="$CONTAINER_NAME_OVERRIDE"
else
  CONTAINER_NAME=$(echo "$LIFERAY_ROOT" | sed -e 's:.*/::' -e 's/[\.]/-/g')
fi
container_exists=$(docker ps -a --format '{{.Names}}' | grep -x "$CONTAINER_NAME" >/dev/null 2>&1 && echo "Y" || echo "N")
container_running=$(docker ps --format '{{.Names}}' | grep -x "$CONTAINER_NAME" >/dev/null 2>&1 && echo "Y" || echo "N")

STOP_CONTAINER_DEFAULT=Y
if [[ -n "$STOP_FLAG" ]]; then
  STOP_CONTAINER=Y
elif [[ -n "$NO_STOP_FLAG" ]]; then
  STOP_CONTAINER=N
else
  read_config "Stop container during restore" STOP_CONTAINER "$STOP_CONTAINER_DEFAULT"
fi

if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
  if [[ -z "$DELETE_STATE_FLAG" ]]; then
    DELETE_STATE_FLAG=1
  fi
  if [[ -z "$DELETE_ES_FLAG" ]]; then
    DELETE_ES_FLAG=1
  fi
fi

if [[ "${STOP_CONTAINER:u}" == "Y" && "$container_running" == "Y" ]]; then
  info_custom "${Yellow}Stopping ${Green}$CONTAINER_NAME"
  docker stop "$CONTAINER_NAME" >/dev/null 2>&1
  ! wait_for_container_stop "$CONTAINER_NAME" && _die "Container failed to stop within timeout."
  sleep 2
fi

if [[ "$NON_INTERACTIVE" -eq 1 && "${STOP_CONTAINER:u}" == "Y" && -n "$DELETE_STATE_FLAG" ]]; then
  if [[ -d "$STATE_VOLUME" ]]; then
    info_custom "${Yellow}Deleting OSGi state folder:${Color_Off} $STATE_VOLUME"
    rm -rf "$STATE_VOLUME"
  fi
fi

if [[ "$NON_INTERACTIVE" -eq 1 && "${STOP_CONTAINER:u}" == "Y" && -n "$DELETE_ES_FLAG" ]]; then
  es_dir="$DATA_VOLUME/elasticsearch7"
  if [[ -d "$es_dir" ]]; then
    info_custom "${Yellow}Deleting Elasticsearch data folder:${Color_Off} $es_dir"
    rm -rf "$es_dir"
  fi
fi

if [[ "$NON_INTERACTIVE" -ne 1 && "${STOP_CONTAINER:u}" == "Y" ]]; then
  read_config "Delete OSGi state" DELETE_STATE_CHOICE "Y"
  if [[ "${DELETE_STATE_CHOICE:u}" == "Y" ]]; then
    if [[ -d "$STATE_VOLUME" ]]; then
      info_custom "${Yellow}Deleting OSGi state folder:${Color_Off} $STATE_VOLUME"
      rm -rf "$STATE_VOLUME"
    fi
  fi
  read_config "Delete Elasticsearch" DELETE_ES_CHOICE "Y"
  if [[ "${DELETE_ES_CHOICE:u}" == "Y" ]]; then
    es_dir="$DATA_VOLUME/elasticsearch7"
    if [[ -d "$es_dir" ]]; then
      info_custom "${Yellow}Deleting Elasticsearch data folder:${Color_Off} $es_dir"
      rm -rf "$es_dir"
    fi
  fi
fi

snapshot_type=""
if [[ -f "$CHECKPOINT_DIR/meta" ]]; then
  snapshot_type=$(sed -n 's/^type=//p' "$CHECKPOINT_DIR/meta" | head -n1)
fi

meta_version=1
if [[ -f "$CHECKPOINT_DIR/meta" ]]; then
  mv_line=$(sed -n 's/^meta_version=//p' "$CHECKPOINT_DIR/meta" | head -n1)
  [[ -n "$mv_line" ]] && meta_version="$mv_line"
fi

postgres_dump=""
mysql_dump=""
files_archive=""
hypers_archive=""

if [[ -f "$CHECKPOINT_DIR/meta" ]]; then
  meta_db_dump=$(sed -n 's/^db_dump=//p' "$CHECKPOINT_DIR/meta" | head -n1)
  meta_files_arc=$(sed -n 's/^files_archive=//p' "$CHECKPOINT_DIR/meta" | head -n1)
  if [[ -n "$meta_db_dump" ]]; then
    db_path="$CHECKPOINT_DIR/$meta_db_dump"
    case "$snapshot_type" in
      postgresql|postgres|pg) postgres_dump="$db_path" ;;
      mysql|mariadb) mysql_dump="$db_path" ;;
    esac
  fi
  if [[ -n "$meta_files_arc" ]]; then
    files_archive="$CHECKPOINT_DIR/$meta_files_arc"
  fi
fi

# Fallbacks if meta is missing hints
[[ -z "$postgres_dump" ]] && postgres_dump=$(find "$CHECKPOINT_DIR" -maxdepth 1 -name "db-postgresql.sql.*" -o -name "postgresql.sql.*" | head -n1)
[[ -z "$mysql_dump" ]] && mysql_dump=$(find "$CHECKPOINT_DIR" -maxdepth 1 -name "db-mysql.sql.*" -o -name "mysql.sql.*" | head -n1)
[[ -z "$files_archive" ]] && files_archive=$(find "$CHECKPOINT_DIR" -maxdepth 1 -name "files.tar.*" | head -n1)
[[ -z "$hypers_archive" ]] && hypers_archive=$(find "$CHECKPOINT_DIR" -maxdepth 1 -name "filesystem.tar.*" | head -n1)

snapshot_format="standard"
if [[ -f "$CHECKPOINT_DIR/meta" ]]; then
  fmt_line=$(sed -n 's/^format=//p' "$CHECKPOINT_DIR/meta" | head -n1)
  [[ -n "$fmt_line" ]] && snapshot_format="$fmt_line"
fi

if (( meta_version < MIN_META_VERSION )) && [[ "$ALLOW_LEGACY" -ne 1 ]]; then
  _die "Backup meta_version=$meta_version is older than the minimum supported ($MIN_META_VERSION). Use --allow-legacy."
fi

if [[ "$snapshot_type" == "hypersonic" ]]; then
  archive="$hypers_archive"
  [[ -z "$archive" ]] && _die "Missing archive in $CHECKPOINT_DIR"
  info "Restoring hypersonic snapshot"
  find "$LIFERAY_ROOT" -mindepth 1 -maxdepth 1 ! -name "backups" -exec rm -rf {} +
  safe_extract "$archive" "$LIFERAY_ROOT"
else
  jdbc_url=$(read_db_prop "jdbc.default.url")
  jdbc_user=$(read_db_prop "jdbc.default.username")
  jdbc_pass=$(read_db_prop "jdbc.default.password")

  if echo "$snapshot_type" | grep -qi "postgresql"; then
    dump_file="${postgres_dump:-$CHECKPOINT_DIR/database.gz}"
    [[ ! -f "$dump_file" ]] && _die "PostgreSQL dump not found."
    dbname=$(echo "$jdbc_url" | sed -E 's#^jdbc:postgresql://[^/]+/([^?]+).*#\1#')
    pghost="${PG_HOST_OVERRIDE:-localhost}"
    pgport="${PG_PORT_OVERRIDE:-5432}"
    [[ "$pghost" == "host.docker.internal" ]] && pghost="localhost"

    info "Resetting PostgreSQL database: $dbname"
    PGPASSWORD="$jdbc_pass" psql -h "$pghost" -p "$pgport" -U "$jdbc_user" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$dbname' AND pid <> pg_backend_pid();" >/dev/null 2>&1
    PGPASSWORD="$jdbc_pass" psql -h "$pghost" -p "$pgport" -U "$jdbc_user" -d postgres -c "DROP DATABASE IF EXISTS \"$dbname\"; CREATE DATABASE \"$dbname\" WITH TEMPLATE template0 ENCODING 'UTF8';" >/dev/null

    info "Importing PostgreSQL dump..."
    if [[ "$dump_file" == *.gz ]]; then gunzip -c "$dump_file" | PGPASSWORD="$jdbc_pass" psql -h "$pghost" -p "$pgport" -U "$jdbc_user" -d "$dbname" >/dev/null
    else cat "$dump_file" | PGPASSWORD="$jdbc_pass" psql -h "$pghost" -p "$pgport" -U "$jdbc_user" -d "$dbname" >/dev/null; fi
  elif echo "$snapshot_type" | grep -qi "mysql"; then
    dump_file="${mysql_dump:-$CHECKPOINT_DIR/database.gz}"
    [[ ! -f "$dump_file" ]] && _die "MySQL dump not found."
    dbname=$(echo "$jdbc_url" | sed -E 's#^jdbc:mysql://[^/]+/([^?]+).*#\1#')
    myhost="${MY_HOST_OVERRIDE:-localhost}"
    myport="${MY_PORT_OVERRIDE:-3306}"
    [[ "$myhost" == "host.docker.internal" ]] && myhost="localhost"

    info "Resetting MySQL database: $dbname"
    mysql -h "$myhost" -P "$myport" -u "$jdbc_user" -p"$jdbc_pass" -e "DROP DATABASE IF EXISTS \`$dbname\`; CREATE DATABASE \`$dbname\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" >/dev/null
    info "Importing MySQL dump..."
    if [[ "$dump_file" == *.gz ]]; then gunzip -c "$dump_file" | mysql -h "$myhost" -P "$myport" -u "$jdbc_user" -p"$jdbc_pass" "$dbname" >/dev/null
    else cat "$dump_file" | mysql -h "$myhost" -P "$myport" -u "$jdbc_user" -p"$jdbc_pass" "$dbname" >/dev/null; fi
  fi

  if [[ "$snapshot_format" == "liferay-cloud" ]]; then
    vol="$CHECKPOINT_DIR/volume.tgz"
    if [[ -f "$vol" ]]; then mkdir -p "$LIFERAY_ROOT/data" && safe_extract "$vol" "$LIFERAY_ROOT/data"; fi
  else
    [[ -n "$files_archive" ]] && safe_extract "$files_archive" "$LIFERAY_ROOT"
  fi
fi

if [[ "${DELETE_AFTER_FLAG}" == 1 ]]; then rm -rf "$CHECKPOINT_DIR"; fi
if [[ "${STOP_CONTAINER:u}" == "Y" && "$container_exists" == "Y" ]]; then docker start "$CONTAINER_NAME" >/dev/null 2>&1; fi
info_custom "${Green}Restore complete${Color_Off}"
