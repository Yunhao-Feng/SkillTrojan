nohup python ehr_run.py > ehr.txt 2>&1 &
pkill -f "python ehr_run.py"

nohup python run_attack.py > attack.txt 2>&1 &
