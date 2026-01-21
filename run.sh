nohup python ehr_run.py > ehr.txt 2>&1 &
pkill -f "python swe_run_mp.py"