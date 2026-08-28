#!/usr/bin/env bash
set -euo pipefail
PROTO_DIR="./proto"
PROTO_FILE="${PROTO_DIR}/gepa.proto"
OUT_DIR="./gepa_rpc/generated"
mkdir -p "${OUT_DIR}"
if [[ ! -f "${PROTO_FILE}" ]]; then
  echo "Error: ${PROTO_FILE} not found. Run this script from the project root." >&2
  exit 1
fi
python -m grpc_tools.protoc \
  -I./proto \
  --python_out=./gepa_rpc/generated \
  --pyi_out=./gepa_rpc/generated \
  --grpc_python_out=./gepa_rpc/generated \
  ./proto/gepa.proto
if [[ ! -f "${OUT_DIR}/gepa_pb2_grpc.py" ]]; then
  echo "Error: expected ${OUT_DIR}/gepa_pb2_grpc.py was not generated." >&2
  exit 1
fi
sed -i.bak 's/^import gepa_pb2 as gepa__pb2$/from gepa_rpc.generated import gepa_pb2 as gepa__pb2/' "${OUT_DIR}/gepa_pb2_grpc.py"
rm -f "${OUT_DIR}/gepa_pb2_grpc.py.bak"
# grpc stubs don't expose grpc.experimental; suppress false-positive type errors in this generated file.
python3 -c "
import sys
path = '${OUT_DIR}/gepa_pb2_grpc.py'
with open(path) as f:
    content = f.read()
if '# type: ignore' not in content[:100]:
    content = '# type: ignore\n' + content
with open(path, 'w') as f:
    f.write(content)
"
cp "${PROTO_FILE}" ./sdk/typescript/proto/gepa.proto
cp "${PROTO_FILE}" ./sdk/rust/proto/gepa.proto
echo "Proto compilation succeeded. Generated files:"
echo "  - ${OUT_DIR}/gepa_pb2.py"
echo "  - ${OUT_DIR}/gepa_pb2.pyi"
echo "  - ${OUT_DIR}/gepa_pb2_grpc.py"
echo "  - sdk/typescript/proto/gepa.proto (synced)"
echo "  - sdk/rust/proto/gepa.proto (synced)"
