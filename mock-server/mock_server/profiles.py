"""Fixed characteristics of the simulated OceanProtect appliance."""

GB = 1024**3
TB = 1024**4
SECTOR = 512
SECTORS_PER_GB = GB // SECTOR

MODELS = ("OceanProtect X3000", "OceanProtect X6000", "OceanProtect X8000", "OceanProtect X9000")

# Ratios are N:1. total reduction = dedupe * compression (20:1 - 42:1)
DEDUPE_RANGE = (8.0, 12.0)
COMPRESSION_RANGE = (2.5, 3.5)

# Backup ingestion is throughput oriented, not IOPS oriented.
WRITE_MBPS_RANGE = (2_500, 8_000)
READ_MBPS_RANGE = (400, 2_500)
IOPS_RANGE = (8_000, 40_000)
STREAMS_PER_CONTROLLER = (16, 96)

BACKUP_TYPES = {1: "full", 2: "incremental"}
