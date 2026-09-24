#!/usr/bin/env bash
# Phase 0 probe for plans/PLAN_MODELARK_ASSET_LIBRARY.md:
#   ListAssetGroups -> CreateAssetGroup (AIGC) -> CreateAsset -> poll GetAsset.
# Requires BYTEPLUS_MODELARK_ACCESS_KEY / _SECRET_KEY, plus _SESSION_TOKEN for
# TSP/STS temporary credentials. Never deletes anything.
#
# Usage: scripts/asset_library_probe.sh <public-https-image-url> [group-name]
# The image must be a fictional/synthetic character: Virtual Portrait (AIGC)
# assets must not resemble any real person.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
CALL="python3 $DIR/ark_openapi_sign.py"
URL="${1:?usage: $0 <public-https-image-url> [group-name]}"
GROUP_NAME="${2:-mcp-api-test}"
PROJECT="${BYTEPLUS_MODELARK_PROJECT_NAME:-default}"
OUT="${TMPDIR:-/tmp}/asset_library_probe"
mkdir -p "$OUT"
field() { python3 -c "import sys,json; d=json.load(sys.stdin); e=d.get('ResponseMetadata',{}).get('Error'); print('ERROR: '+json.dumps(e)) if e else print(d['Result'].get('$1',''))"; }

echo "== 1. ListAssetGroups (read-only credential check)"
$CALL ListAssetGroups "{\"Filter\":{\"GroupType\":\"AIGC\"},\"MaxResults\":1,\"ProjectName\":\"$PROJECT\"}" | tee "$OUT/list.json"; echo
grep -q '"Error"' "$OUT/list.json" && { echo "Credential check failed; stopping."; exit 1; }

echo "== 2. CreateAssetGroup (Virtual Portrait / AIGC)"
BODY=$(python3 -c "import json,sys; print(json.dumps({'Name':sys.argv[1],'Description':'ark-mcp asset library probe (synthetic character)','GroupType':'AIGC','ProjectName':sys.argv[2]}))" "$GROUP_NAME" "$PROJECT")
GROUP_ID=$($CALL CreateAssetGroup "$BODY" | tee "$OUT/group.json" | field Id)
echo "group: $GROUP_ID"; [[ "$GROUP_ID" == ERROR* ]] && exit 1

echo "== 3. CreateAsset"
BODY=$(python3 -c "import json,sys; print(json.dumps({'GroupId':sys.argv[1],'URL':sys.argv[2],'AssetType':'Image','Name':'probe-image','ProjectName':sys.argv[3]}))" "$GROUP_ID" "$URL" "$PROJECT")
ASSET_ID=$($CALL CreateAsset "$BODY" | tee "$OUT/asset.json" | field Id)
echo "asset: $ASSET_ID"; [[ "$ASSET_ID" == ERROR* ]] && exit 1

echo "== 4. Poll GetAsset until Active/Failed (max ~3 min)"
STATUS=""
for i in $(seq 1 36); do
  STATUS=$($CALL GetAsset "{\"Id\":\"$ASSET_ID\",\"ProjectName\":\"$PROJECT\"}" | tee "$OUT/get.json" | field Status)
  echo "  [$i] $STATUS"
  [[ "$STATUS" == "Active" || "$STATUS" == "Failed" || "$STATUS" == ERROR* ]] && break
  sleep 5
done
echo; echo "RESULT group=$GROUP_ID asset=$ASSET_ID status=$STATUS uri=asset://$ASSET_ID"
echo "Raw responses: $OUT"
