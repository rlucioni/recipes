deploy:
	zappa deploy prod

embeddings:
	python -c 'import app; app.embed_recipes();'

lint:
	flake8 .

logs:
	zappa tail prod --since 1d --disable-keep-open

package:
	zappa package prod

prune:
	python prune.py

requirements:
	pip install -r requirements.txt

rollback:
	zappa rollback prod -n 1

run:
	flask run --port 5001 --debug

schedule:
	zappa schedule prod

status:
	zappa status prod

tunnel:
	ngrok http 5001

undeploy:
	zappa undeploy prod --remove-logs

unschedule:
	zappa unschedule prod

update:
	zappa update prod
