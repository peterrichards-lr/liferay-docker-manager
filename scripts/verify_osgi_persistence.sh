#!/usr/bin/env bash
#
# OSGi State Persistence Verification Script
#times the boot up sequence and compares the first run (cold) vs second run (warm).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source colors
if [ -f "$SCRIPT_DIR/colors.sh" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/colors.sh"
else
    UI_COLOR_OFF=''
    UI_BGREEN=''
    UI_BYELLOW=''
    UI_BRED=''
    UI_BCYAN=''
fi

info() { echo -e "${UI_BCYAN}ℹ $1${UI_COLOR_OFF}"; }
success() { echo -e "${UI_BGREEN}✅ $1${UI_COLOR_OFF}"; }
warning() { echo -e "${UI_BYELLOW}⚠️ $1${UI_COLOR_OFF}"; }
error() { echo -e "${UI_BRED}❌ $1${UI_COLOR_OFF}"; }

# Defaults
TAG="2026.q1.7-lts"
PROJECT_NAME="osgi-persist-test"
WORK_DIR="$PROJECT_ROOT/e2e-work-dir/$PROJECT_NAME"
TIMEOUT=600

# Parse options
while [[ $# -gt 0 ]]; do
    case $1 in
        --tag)
            TAG="$2"
            shift 2
            ;;
        --project)
            PROJECT_NAME="$2"
            WORK_DIR="$PROJECT_ROOT/e2e-work-dir/$PROJECT_NAME"
            shift 2
            ;;
        *)
            error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check Docker
if ! docker info >/dev/null 2>&1; then
    error "Docker daemon is not running. Please start Docker."
    exit 1
fi

# Activate virtualenv if present
if [ -d "$PROJECT_ROOT/.venv" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# Clean up any existing container/project
info "Cleaning up any old test stacks..."
docker rm -f "$PROJECT_NAME" 2>/dev/null || true
rm -rf "$WORK_DIR"

info "Initializing mock project: $PROJECT_NAME (Tag: $TAG)"
mkdir -p "$WORK_DIR/files"
touch "$WORK_DIR/files/portal-ext.properties"

# Generate metadata
{
    echo "tag=$TAG"
    echo "container_name=$PROJECT_NAME"
    echo "db_type=postgresql"
} > "$WORK_DIR/.liferay-docker.meta"

# Python log parser helper
parse_log_timings() {
    local log_file="$1"
    python3 -c "
import sys
import re
from datetime import datetime

log_file = '$log_file'
with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

pattern = r'^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}[.,]\d{3})'

start_osgi = None
end_osgi = None
first_line_ts = None
catalina_ts = None

for line in content.splitlines():
    if 'Starting Liferay Module Framework' in line:
        m = re.match(pattern, line)
        if m:
            start_osgi = m.group(1)
    elif 'Liferay Module Framework started' in line:
        m = re.match(pattern, line)
        if m:
            end_osgi = m.group(1)
    
    m = re.match(pattern, line)
    if m:
        if first_line_ts is None:
            first_line_ts = m.group(1)
        if 'org.apache.catalina.startup.Catalina.start Server startup in' in line:
            catalina_ts = m.group(1)

def parse_ts(ts):
    if not ts:
        return None
    ts = ts.replace(',', '.')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%d-%b-%Y %H:%M:%S.%f'):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            pass
    return None

t_start_osgi = parse_ts(start_osgi)
t_end_osgi = parse_ts(end_osgi)
t_first = parse_ts(first_line_ts)
t_catalina = parse_ts(catalina_ts)

osgi_duration = (t_end_osgi - t_start_osgi).total_seconds() if t_start_osgi and t_end_osgi else -1
total_duration = (t_catalina - t_first).total_seconds() if t_first and t_catalina else -1

print(f'OSGI_DURATION={osgi_duration}')
print(f'TOTAL_DURATION={total_duration}')
"
}

wait_for_liferay() {
    local label="$1"
    local log_file="$2"
    local start_time
    start_time=$(date +%s)
    
    info "Waiting for Liferay container '$PROJECT_NAME' to boot ($label)..."
    
    while true; do
        local current_time
        current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [ "$elapsed" -ge "$TIMEOUT" ]; then
            error "Timeout reached ($TIMEOUT s) waiting for Liferay boot."
            docker logs "$PROJECT_NAME" > "$log_file"
            exit 1
        fi
        
        # Check container status
        if ! docker ps --format '{{.Names}}' | grep -q "^${PROJECT_NAME}$"; then
            error "Container '$PROJECT_NAME' is no longer running."
            docker logs "$PROJECT_NAME" > "$log_file" 2>&1 || true
            exit 1
        fi
        
        # Check logs for Tomcat startup marker
        local logs
        logs=$(docker logs --tail 100 "$PROJECT_NAME" 2>&1)
        if echo "$logs" | grep -q "org.apache.catalina.startup.Catalina.start Server startup in"; then
            success "Liferay is fully ready!"
            docker logs "$PROJECT_NAME" > "$log_file"
            break
        fi
        
        sleep 5
    done
}

# --- RUN 1: Cold Boot (No OSGi cache) ---
info "=== RUN 1: Cold Boot (OSGi Persistence Enabled) ==="
python3 "$PROJECT_ROOT/liferay_docker.py" run "$WORK_DIR" --persist-osgi --no-wait -y

LOG_RUN1="/tmp/ldm_osgi_run1.log"
wait_for_liferay "Cold Boot" "$LOG_RUN1"

# Parse timings for run 1
TIMINGS_RUN1=$(parse_log_timings "$LOG_RUN1")
eval "$TIMINGS_RUN1"
OSGI_RUN1="$OSGI_DURATION"
TOTAL_RUN1="$TOTAL_DURATION"

info "Cold Boot Total Startup Time: ${TOTAL_RUN1}s"
info "Cold Boot OSGi Bundle Resolution Time: ${OSGI_RUN1}s"

# Graceful stop
info "Stopping container gracefully..."
python3 "$PROJECT_ROOT/liferay_docker.py" stop "$WORK_DIR" -y

# Verify host OSGi cache exists
info "Checking state folder on the host..."
if [ -d "$WORK_DIR/osgi/state" ] && [ "$(ls -A "$WORK_DIR/osgi/state")" ]; then
    success "OSGi state folder exists and is populated on the host machine!"
else
    error "OSGi state folder was not persisted on the host."
    exit 1
fi

# --- RUN 2: Warm Boot (OSGi cache persisted) ---
info "=== RUN 2: Warm Boot (OSGi Persistence Enabled) ==="
python3 "$PROJECT_ROOT/liferay_docker.py" run "$WORK_DIR" --persist-osgi --no-wait -y

LOG_RUN2="/tmp/ldm_osgi_run2.log"
wait_for_liferay "Warm Boot" "$LOG_RUN2"

# Parse timings for run 2
TIMINGS_RUN2=$(parse_log_timings "$LOG_RUN2")
eval "$TIMINGS_RUN2"
OSGI_RUN2="$OSGI_DURATION"
TOTAL_RUN2="$TOTAL_DURATION"

info "Warm Boot Total Startup Time: ${TOTAL_RUN2}s"
info "Warm Boot OSGi Bundle Resolution Time: ${OSGI_RUN2}s"

# Graceful cleanup
info "Cleaning up verification stack..."
python3 "$PROJECT_ROOT/liferay_docker.py" down "$WORK_DIR" -y

# Output Report
echo -e "\n=== OSGi STATE PERSISTENCE VERIFICATION REPORT ==="
echo -e "Liferay Image Tag:  $TAG"
echo -e "--------------------------------------------------"
echo -e "Metric                    | Cold Boot | Warm Boot | Reduction"
echo -e "--------------------------+-----------+-----------+----------"

# OSGi Duration format
if (( $(echo "$OSGI_RUN1 > 0" | bc -l) )) && (( $(echo "$OSGI_RUN2 > 0" | bc -l) )); then
    OSGI_REDUCTION=$(python3 -c "print(f'{($OSGI_RUN1 - $OSGI_RUN2):.2f}s ({((($OSGI_RUN1 - $OSGI_RUN2) / $OSGI_RUN1) * 100):.1f}%)')")
    printf "OSGi Bundle Resolution    | %9.2fs | %9.2fs | %s\n" "$OSGI_RUN1" "$OSGI_RUN2" "$OSGI_REDUCTION"
else
    printf "OSGi Bundle Resolution    |      N/A  |      N/A  | N/A\n"
fi

# Total Duration format
if (( $(echo "$TOTAL_RUN1 > 0" | bc -l) )) && (( $(echo "$TOTAL_RUN2 > 0" | bc -l) )); then
    TOTAL_REDUCTION=$(python3 -c "print(f'{($TOTAL_RUN1 - $TOTAL_RUN2):.2f}s ({((($TOTAL_RUN1 - $TOTAL_RUN2) / $TOTAL_RUN1) * 100):.1f}%)')")
    printf "Total Container Startup   | %9.2fs | %9.2fs | %s\n" "$TOTAL_RUN1" "$TOTAL_RUN2" "$TOTAL_REDUCTION"
else
    printf "Total Container Startup   |      N/A  |      N/A  | N/A\n"
fi
echo -e "--------------------------------------------------"

# Verification Assertion
if (( $(echo "$OSGI_RUN2 < $OSGI_RUN1" | bc -l) )); then
    success "VERIFICATION SUCCESS: OSGi bundle resolution is faster on warm startup!"
else
    warning "VERIFICATION WARNING: Warm startup did not show faster OSGi bundle resolution. Check CPU/disk throttling."
fi
