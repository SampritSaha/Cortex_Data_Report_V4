import sys
from pathlib import Path
from datetime import datetime,timedelta
sys.path.insert(0,str(Path(__file__).parent/'synthetic_data_generators'))
from generator import generate
end=datetime(2026,9,27,11,30,0)
ranges={'PRJ-001':7,'PRJ-002':2,'PRJ-003':30,'PRJ-004':30,'PRJ-005':7,'PRJ-006':60,'PRJ-007':30}
for pid,days in ranges.items():
    print('Generating',pid)
    generate(pid,end=end,start=end-timedelta(days=days),overwrite=True)
