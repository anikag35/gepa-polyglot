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
  --grpc_python_out=./gepa_rpc/generated \
  ./proto/gepa.proto
if [[ ! -f "${OUT_DIR}/gepa_pb2_grpc.py" ]]; then
  echo "Error: expected ${OUT_DIR}/gepa_pb2_grpc.py was not generated." >&2
  exit 1
fi
sed -i.bak 's/^import gepa_pb2 as gepa__pb2$/from gepa_rpc.generated import gepa_pb2 as gepa__pb2/' "${OUT_DIR}/gepa_pb2_grpc.py"
rm -f "${OUT_DIR}/gepa_pb2_grpc.py.bak"
echo "Proto compilation succeeded. Generated files:"
echo "  - ${OUT_DIR}/gepa_pb2.py"
echo "  - ${OUT_DIR}/gepa_pb2_grpc.py"
