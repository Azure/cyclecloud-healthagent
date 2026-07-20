from enum import Enum

class GHRCategory(Enum):
    # Operational
    RESET = "Resource.Hpc.Reset"
    REBOOT = "Resource.Hpc.Reboot"
    # GPU
    MISSING_GPU = "Resource.Hpc.Unhealthy.HpcMissingGpu"
    DCGM_DIAG_FAILURE = "Resource.Hpc.Unhealthy.HpcGpuDcgmDiagFailure"
    ROW_REMAP_FAILURE = "Resource.Hpc.Unhealthy.HpcRowRemapFailure"
    INFOROM_CORRUPTION = "Resource.Hpc.Unhealthy.HpcInforomCorruption"
    XID95_UNCONTAINED_ECC = "Resource.Hpc.Unhealthy.XID95UncontainedECCError"
    XID94_CONTAINED_ECC = "Resource.Hpc.Unhealthy.XID94ContainedECCError"
    XID79_FALLEN_OFF_BUS = "Resource.Hpc.Unhealthy.XID79FallenOffBus"
    XID48_DOUBLE_BIT_ECC = "Resource.Hpc.Unhealthy.XID48DoubleBitECC"
    UNHEALTHY_GPU_NVIDIASMI = "Resource.Hpc.Unhealthy.UnhealthyGPUNvidiasmi"
    NVLINK = "Resource.Hpc.Unhealthy.NvLink"
    DCGMI_THERMAL = "Resource.Hpc.Unhealthy.HpcDcgmiThermalReport"
    ECC_PAGE_RETIRE_FULL = "Resource.Hpc.Unhealthy.ECCPageRetirementTableFull"
    DBE_OVER_LIMIT = "Resource.Hpc.Unhealthy.DBEOverLimit"
    GPU_XID_ERROR = "Resource.Hpc.Unhealthy.GpuXIDError"
    EROT_FAILURE = "Resource.Hpc.Unhealthy.EROTFailure"
    GPU_MEM_BW_FAILURE = "Resource.Hpc.Unhealthy.GPUMemoryBWFailure"
    # Network / InfiniBand
    MISSING_IB = "Resource.Hpc.Unhealthy.MissingIB"
    IB_PERFORMANCE = "Resource.Hpc.Unhealthy.IBPerformance"
    IB_PORT_DOWN = "Resource.Hpc.Unhealthy.IBPortDown"
    IB_PORT_FLAPPING = "Resource.Hpc.Unhealthy.IBPortFlapping"
    # Generic
    GENERIC_FAILURE = "Resource.Hpc.Unhealthy.HpcGenericFailure"
    MANUAL_INVESTIGATION = "Resource.Hpc.Unhealthy.ManualInvestigation"
    # CPU
    CPU_PERFORMANCE = "Resource.Hpc.Unhealthy.CPUPerformance"