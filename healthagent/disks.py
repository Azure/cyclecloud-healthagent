import os
import logging
from healthagent import healthcheck
from healthagent.scheduler import Scheduler
from healthagent.healthmodule import HealthModule
from healthagent.config import DiskConfig
from healthagent.reporter import Reporter, HealthStatus, HealthReport

log = logging.getLogger(__name__)


class DiskHealthChecks(HealthModule):

    def __init__(self, reporter: Reporter, config: 'DiskConfig | None' = None):
        super().__init__(reporter, config or DiskConfig())
        self.config: DiskConfig = self.config

    async def create(self):
        Scheduler.add_task(self.check_mounts)

    @healthcheck("DiskSpaceCheck", description="Check mount space and inode usage")
    @Scheduler.periodic(60)
    async def check_mounts(self):
        name = self.check_mounts.report_name
        report = HealthReport()
        details = []

        for mountpoint, mount_cfg in self.config.mounts.items():
            try:
                st = os.statvfs(mountpoint)
            except OSError as e:
                log.error(f"Failed to statvfs {mountpoint}: {e}")
                continue

            # Space check
            if st.f_blocks > 0:
                used = st.f_blocks - st.f_bavail
                usage_pct = (used / st.f_blocks) * 100

                if mount_cfg.space_pct.error is not None and usage_pct >= mount_cfg.space_pct.error:
                    report.escalate(HealthStatus.ERROR)
                    details.append(f"ERROR: {mountpoint} disk usage {usage_pct:.1f}% >= {mount_cfg.space_pct.error}%")
                elif mount_cfg.space_pct.warning is not None and usage_pct >= mount_cfg.space_pct.warning:
                    report.escalate(HealthStatus.WARNING)
                    details.append(f"WARNING: {mountpoint} disk usage {usage_pct:.1f}% >= {mount_cfg.space_pct.warning}%")

            # Inode check
            if st.f_files > 0:
                inode_usage_pct = ((st.f_files - st.f_ffree) / st.f_files) * 100

                if mount_cfg.inode_pct.error is not None and inode_usage_pct >= mount_cfg.inode_pct.error:
                    report.escalate(HealthStatus.ERROR)
                    details.append(f"ERROR: {mountpoint} inode usage {inode_usage_pct:.1f}% >= {mount_cfg.inode_pct.error}%")
                elif mount_cfg.inode_pct.warning is not None and inode_usage_pct >= mount_cfg.inode_pct.warning:
                    report.escalate(HealthStatus.WARNING)
                    details.append(f"WARNING: {mountpoint} inode usage {inode_usage_pct:.1f}% >= {mount_cfg.inode_pct.warning}%")

        if details:
            report.details = "\n".join(details)
        if report.status != HealthStatus.OK:
            report.description = "Disk health issues detected"

        await self.reporter.update_report(name=name, report=report)
        return {name: report.view()}