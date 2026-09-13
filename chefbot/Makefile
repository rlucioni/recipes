include .env
export $(shell sed 's/=.*//' .env)

deploy:
	@rm -f .env.yaml
	@echo "generating .env.yaml from .env..."
	@echo "GEMINI_API_KEY: ${GEMINI_API_KEY}" >> .env.yaml
	@echo "SLACK_BOT_TOKEN: ${SLACK_BOT_TOKEN}" >> .env.yaml
	@echo "SLACK_SIGNING_SECRET: ${SLACK_SIGNING_SECRET}" >> .env.yaml
	@echo "deploying to GCP..."
	npm run deploy
	@rm .env.yaml
	@echo "deployment complete!"

embeddings:
	npm run embed

enable:
	gcloud services enable \
	cloudfunctions.googleapis.com \
	run.googleapis.com \
	cloudbuild.googleapis.com \
	artifactregistry.googleapis.com \
	cloudtasks.googleapis.com

lint:
	npm run lint

logs:
	npm run logs

ping:
	curl localhost:8080

prettier:
	npm run prettier

# Creates the Cloud Tasks queue used to hand off thinking from the Slack event
# handler, and lets the function's default service account enqueue tasks.
queue:
	gcloud tasks queues create chefbot --location=us-central1 --max-attempts=1
	gcloud projects add-iam-policy-binding $$(gcloud config get-value project) \
	--member=serviceAccount:$$(gcloud projects describe $$(gcloud config get-value project) --format='value(projectNumber)')-compute@developer.gserviceaccount.com \
	--role=roles/cloudtasks.enqueuer

serve:
	npm run serve

tunnel:
	ngrok http 8080

undeploy:
	npm run undeploy

url:
	npm run url --silent
