#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


SOURCE = Path("/home/maoyd/douge_ai_agent/tools/generate_finsight_beauty_avatar_candidates.py")
spec = importlib.util.spec_from_file_location("beauty_candidates", SOURCE)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def main() -> None:
    module.wait_ready()
    module.OUT_DIR.mkdir(parents=True, exist_ok=True)

    selected = [job for job in module.JOBS if not job["slug"].startswith("analysis-")]
    final_paths = []
    for job in selected:
        target = module.OUT_DIR / f"finsight-{job['slug']}-source-magenta.png"
        if target.exists():
            final_paths.append((job, target))
            print(f"skip existing {job['slug']}: {target}")
            continue
        prompt_id = module.submit(job)
        source = module.wait_output(prompt_id)
        import shutil

        shutil.copy2(source, target)
        final_paths.append((job, target))
        print(f"{job['agent']} {job['slug']}: {target}")

    all_paths = []
    for job in module.JOBS:
        target = module.OUT_DIR / f"finsight-{job['slug']}-source-magenta.png"
        if target.exists():
            all_paths.append((job, target))
    print(f"contact_sheet: {module.make_contact_sheet(all_paths)}")


if __name__ == "__main__":
    main()
