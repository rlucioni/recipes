deploy:
	zappa deploy prod

lint:
	flake8 .

logs:
	zappa tail prod --since 1d --disable-keep-open

prune:
	python prune.py

requirements:
	pip install -r requirements.txt

rollback:
	zappa rollback prod -n 1

run:
	flask run

schedule:
	zappa schedule prod

status:
	zappa status prod

tunnel:
	ngrok http 5000

undeploy:
	zappa undeploy prod --remove-logs

unschedule:
	zappa unschedule prod

update:
	zappa update prod
