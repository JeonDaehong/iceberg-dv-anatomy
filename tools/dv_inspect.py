#!/usr/bin/env python3
"""
dv-inspect — Iceberg Deletion Vector 의 Roaring 컨테이너를 해부한다.

Phase 1 의 핵심 도구. 삭제 워크로드가 만들어낸 '실제' 컨테이너 타입 분포를
청크 단위로 덤프한다. README §5.2 참조.

설계 판단: RoaringBitmap Java API 에 리플렉션으로 접근하는 대신,
Roaring 의 portable serialization 포맷을 직접 파싱한다.
  - README 리스크 R5(리플렉션 -> 라이브러리 버전 취약) 제거
  - JVM 없이 동작 -> CI/스크립트에서 자유롭게 사용
  - 컨테이너 타입/카디널리티/run 수/바이트를 '직렬화된 그대로' 관찰 (읽기 경로가 보는 것과 동일)

--------------------------------------------------------------------------
포맷 (아래 두 스펙의 합성)

1) Iceberg DV blob  (iceberg.apache.org/spec — Deletion Vectors)
     magic          4 bytes  = 0xD1 0xD3 0x39 0x64
     bitmap         (아래 2번)
     crc32          4 bytes  (little-endian)
   Puffin blob 의 payload 가 바로 이것이다.

2) Iceberg 64비트 확장 (portable Roaring 의 나열)
     bitmapCount    8 bytes  little-endian
     반복 bitmapCount 회:
       key          4 bytes  little-endian   (position 의 상위 32비트)
       bitmap       portable-format 32비트 Roaring

3) portable 32비트 Roaring  (RoaringFormatSpec)
     cookie 4 bytes:
       (a) 0x0000_3B3B (SERIAL_COOKIE) | ((runContainerCount-1) << 16)
           -> run 컨테이너가 존재. 이어서 (containerCount+7)/8 바이트의 run 비트셋.
       (b) 0x0000_3B3A (SERIAL_COOKIE_NO_RUNCONTAINER)
           -> 이어서 4 bytes containerCount. run 컨테이너 없음.
     descriptive header: containerCount 회 (key:2B, cardinality-1:2B)
     offset header: cookie 가 (b) 이거나 containerCount >= 4 이면
                    containerCount * 4 bytes 의 절대 오프셋
     container data:
       run    : runCount(2B) + runCount * (start:2B, length-1:2B)
       array  : cardinality * 2B
       bitmap : 8192 bytes 고정
--------------------------------------------------------------------------
"""
import argparse
import csv
import json
import os
import struct
import sys
import zlib

DV_MAGIC = b"\xd1\xd3\x39\x64"
# RoaringBitmap 소스의 값. 실제 픽스처 바이트로 확인함:
#   SERIAL_COOKIE_NO_RUNCONTAINER = 12346 = 0x303A
#   SERIAL_COOKIE                 = 12347 = 0x303B
SERIAL_COOKIE_NO_RUN = 12346
SERIAL_COOKIE = 12347
ARRAY_MAX_CARDINALITY = 4096          # 이 이하면 array, 초과면 bitmap
BITMAP_CONTAINER_BYTES = 8192
CHUNK = 1 << 16


class ParseError(Exception):
    pass


class Reader:
    """little-endian 커서."""

    def __init__(self, buf, pos=0):
        self.b = buf
        self.p = pos

    def u16(self):
        v = struct.unpack_from("<H", self.b, self.p)[0]
        self.p += 2
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.p)[0]
        self.p += 4
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.p)[0]
        self.p += 4
        return v

    def u64(self):
        v = struct.unpack_from("<Q", self.b, self.p)[0]
        self.p += 8
        return v

    def take(self, n):
        v = self.b[self.p:self.p + n]
        if len(v) != n:
            raise ParseError(f"버퍼 부족: {n} 바이트 요청, {len(v)} 남음 (offset {self.p})")
        self.p += n
        return v


def parse_roaring32(r: Reader):
    """portable 32비트 Roaring 하나를 파싱해 컨테이너 목록을 돌려준다."""
    start = r.p
    cookie = r.u32()

    run_bitset = None
    if (cookie & 0xFFFF) == SERIAL_COOKIE:
        n_containers = ((cookie >> 16) & 0xFFFF) + 1
        n_run_bytes = (n_containers + 7) // 8
        run_bitset = r.take(n_run_bytes)
    elif cookie == SERIAL_COOKIE_NO_RUN:
        n_containers = r.u32()
    else:
        raise ParseError(f"알 수 없는 Roaring cookie: 0x{cookie:08x} (offset {start})")

    # descriptive header: (key, cardinality-1)
    keys, cards = [], []
    for _ in range(n_containers):
        keys.append(r.u16())
        cards.append(r.u16() + 1)

    # offset header 는 run 컨테이너가 없거나 컨테이너가 4개 이상일 때만 존재
    has_offsets = (run_bitset is None) or (n_containers >= 4)
    if has_offsets:
        r.take(4 * n_containers)

    containers = []
    for i in range(n_containers):
        is_run = False
        if run_bitset is not None:
            is_run = bool(run_bitset[i // 8] & (1 << (i % 8)))

        c_start = r.p
        if is_run:
            n_runs = r.u16()
            runs = []
            for _ in range(n_runs):
                s = r.u16()
                ln = r.u16() + 1
                runs.append((s, ln))
            ctype, n_run_out = "run", n_runs
        elif cards[i] > ARRAY_MAX_CARDINALITY:
            r.take(BITMAP_CONTAINER_BYTES)
            ctype, n_run_out = "bitmap", None
        else:
            r.take(2 * cards[i])
            ctype, n_run_out = "array", None

        containers.append({
            "key": keys[i],
            "container": ctype,
            "cardinality": cards[i],
            "runs": n_run_out,
            "bytes": r.p - c_start,
        })

    return containers, r.p - start


def parse_dv_blob(payload: bytes, verify_crc=True):
    """Iceberg DV blob payload -> (컨테이너 목록, 메타).

    실제 바이트로 확인한 레이아웃 (BitmapPositionDeleteIndex.serialize()):

        length  4 bytes  BIG-endian   <- magic + bitmap 의 길이 (자기 자신·CRC 제외)
        magic   4 bytes  d1 d3 39 64
        bitmap  length-4 bytes        <- 64비트 확장 portable Roaring
        crc     4 bytes  BIG-endian   <- magic + bitmap 에 대한 CRC-32

    magic 이 offset 0 에 바로 오는 형태(길이 프리픽스 없음)도 허용한다.
    """
    meta = {"payload_bytes": len(payload)}

    if payload.startswith(DV_MAGIC):
        # 길이 프리픽스 없는 변형
        body_start, body_end = 0, len(payload) - 4
        meta["length_prefix"] = None
    elif len(payload) >= 8 and payload[4:8] == DV_MAGIC:
        declared = struct.unpack_from(">I", payload, 0)[0]
        body_start = 4
        body_end = 4 + declared
        meta["length_prefix"] = declared
        if body_end + 4 > len(payload):
            raise ParseError(
                f"길이 프리픽스({declared})가 payload({len(payload)})를 벗어납니다")
    else:
        raise ParseError(
            f"DV magic 을 offset 0/4 에서 찾지 못했습니다: "
            f"{payload[:8].hex()} (기대 {DV_MAGIC.hex()})"
        )

    if verify_crc and body_end + 4 <= len(payload):
        body = payload[body_start:body_end]          # magic + bitmap
        got = zlib.crc32(body) & 0xFFFFFFFF
        be = struct.unpack_from(">I", payload, body_end)[0]
        le = struct.unpack_from("<I", payload, body_end)[0]
        meta["crc_ok"] = (got == be) or (got == le)
        meta["crc_endian"] = "big" if got == be else ("little" if got == le else None)
        if not meta["crc_ok"]:
            meta["crc_computed"] = f"{got:08x}"
            meta["crc_stored_be"] = f"{be:08x}"

    r = Reader(payload, body_start + 4)   # 길이 프리픽스와 magic 이후
    n_bitmaps = r.u64()
    meta["bitmap_count"] = n_bitmaps

    out = []
    for _ in range(n_bitmaps):
        hi_key = r.u32()
        containers, _ = parse_roaring32(r)
        for c in containers:
            # 전역 청크 번호 = 상위32비트키 * 65536 + 하위16비트키
            c["chunk"] = hi_key * 65536 + c["key"]
            c["hi_key"] = hi_key
            out.append(c)

    meta["total_cardinality"] = sum(c["cardinality"] for c in out)
    meta["container_bytes"] = sum(c["bytes"] for c in out)
    return out, meta


# ---------------------------------------------------------------- Puffin
PUFFIN_MAGIC = b"PFA1"


def read_puffin(path):
    """Puffin 파일에서 DV blob payload 들을 뽑아낸다.

    Puffin 레이아웃:
      Magic | Blob₁ | Blob₂ | ... | Footer
      Footer = Magic | FooterPayload(JSON, 압축 가능) | payloadSize(4B) | flags(4B) | Magic
    """
    with open(path, "rb") as fh:
        data = fh.read()

    if not data.startswith(PUFFIN_MAGIC):
        raise ParseError(f"Puffin magic 아님: {data[:4]!r}")
    if data[-4:] != PUFFIN_MAGIC:
        raise ParseError("파일 끝 Puffin magic 없음")

    flags = data[-8:-4]
    payload_size = struct.unpack_from("<i", data, len(data) - 12)[0]
    footer_start = len(data) - 12 - payload_size
    footer_json = data[footer_start:footer_start + payload_size]

    # flags 첫 바이트 bit0 = footer payload 가 LZ4 압축됨
    if flags[0] & 0x01:
        raise ParseError(
            "Puffin footer 가 LZ4 압축되어 있습니다. Iceberg 기본 설정에서는 비압축이라 "
            "여기까지 오면 안 됩니다. --raw 로 payload 를 직접 넘기세요."
        )

    meta = json.loads(footer_json.decode("utf-8"))
    blobs = []
    for b in meta.get("blobs", []):
        off, ln = b["offset"], b["length"]
        blobs.append({
            "type": b.get("type"),
            "offset": off,
            "length": ln,
            "properties": b.get("properties", {}),
            "payload": data[off:off + ln],
        })
    return blobs, meta


# ---------------------------------------------------------------- report
def summarize(containers):
    by_type = {}
    for c in containers:
        t = by_type.setdefault(c["container"], {"n": 0, "card": 0, "bytes": 0, "runs": 0})
        t["n"] += 1
        t["card"] += c["cardinality"]
        t["bytes"] += c["bytes"]
        if c["runs"]:
            t["runs"] += c["runs"]
    return by_type


def print_report(containers, meta, name, limit):
    print(f"\n{'=' * 78}\n{name}\n{'=' * 78}")
    for k, v in meta.items():
        print(f"  {k:<20} {v}")

    if not containers:
        print("  (컨테이너 없음)")
        return

    print(f"\n  {'chunk':>8} {'container':<9} {'cardinality':>12} {'density':>9} {'runs':>7} {'bytes':>9}")
    print("  " + "-" * 60)
    for c in containers[:limit]:
        dens = c["cardinality"] / CHUNK * 100
        runs = c["runs"] if c["runs"] is not None else "-"
        print(f"  {c['chunk']:>8} {c['container']:<9} {c['cardinality']:>12,} "
              f"{dens:>8.3f}% {str(runs):>7} {c['bytes']:>9,}")
    if len(containers) > limit:
        print(f"  ... ({len(containers) - limit} 개 생략, 전체는 --csv 로)")

    print(f"\n  [컨테이너 타입 분포]")
    total = len(containers)
    for t, v in sorted(summarize(containers).items()):
        print(f"    {t:<8} {v['n']:>5} 개 ({100*v['n']/total:>5.1f}%)  "
              f"카디널리티 {v['card']:>10,}  {v['bytes']:>10,}B"
              + (f"  run {v['runs']:,}개" if t == "run" else ""))

    print(f"\n  참고: array/bitmap 경계는 카디널리티 {ARRAY_MAX_CARDINALITY:,} "
          f"(= 청크 밀도 {100*ARRAY_MAX_CARDINALITY/CHUNK:.2f}%)")


def main():
    ap = argparse.ArgumentParser(
        description="Iceberg Deletion Vector 의 Roaring 컨테이너를 해부한다.")
    ap.add_argument("paths", nargs="+", help=".puffin 파일 (또는 --raw 로 DV blob payload)")
    ap.add_argument("--raw", action="store_true",
                    help="입력이 Puffin 이 아니라 DV blob payload 자체")
    ap.add_argument("--csv", help="전체 컨테이너를 CSV 로 저장")
    ap.add_argument("--json", help="요약을 JSON 으로 저장")
    ap.add_argument("--limit", type=int, default=40, help="화면 출력 행 수 (기본 40)")
    ap.add_argument("--no-crc", action="store_true", help="CRC 검증 생략")
    args = ap.parse_args()

    rows, summaries = [], []
    for path in args.paths:
        try:
            if args.raw:
                with open(path, "rb") as fh:
                    blobs = [{"type": "raw", "payload": fh.read(), "properties": {}}]
            else:
                blobs, _ = read_puffin(path)
        except (ParseError, OSError) as e:
            print(f"!! {path}: {e}", file=sys.stderr)
            continue

        for i, blob in enumerate(blobs):
            if blob["type"] not in (None, "raw", "deletion-vector-v1"):
                print(f"  (건너뜀: blob type={blob['type']})")
                continue
            try:
                containers, meta = parse_dv_blob(blob["payload"],
                                                 verify_crc=not args.no_crc)
            except ParseError as e:
                print(f"!! {path} blob[{i}]: {e}", file=sys.stderr)
                continue

            meta["blob_type"] = blob["type"]
            for k in ("referenced-data-file", "cardinality"):
                if k in blob.get("properties", {}):
                    meta[k] = blob["properties"][k]

            name = f"{os.path.basename(path)}  blob[{i}]"
            print_report(containers, meta, name, args.limit)

            for c in containers:
                rows.append({"source": os.path.basename(path), "blob": i, **c})
            summaries.append({"source": path, "blob": i, "meta": meta,
                              "by_type": summarize(containers)})

    if args.csv and rows:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nCSV -> {args.csv}  ({len(rows)} 행)")

    if args.json and summaries:
        with open(args.json, "w") as fh:
            json.dump(summaries, fh, indent=2)
        print(f"JSON -> {args.json}")

    if not rows:
        sys.exit(1)


if __name__ == "__main__":
    main()
