include .env
export $(shell sed 's/=.*//' .env)

deploy:
	@rm -f .env.yaml
	@echo "generating .env.yaml from .env..."
	@echo "GEMINI_API_KEY: ${GEMINI_API_KEY}" >> .env.yaml
	@echo "SLACK_BOT_TOKEN: ${SLACK_BOT_TOKEN}" >> .env.yaml
	@echo "SLACK_SIGNING_SECRET: ${SLACK_SIGNING_SECRET}" >> .env.yaml
	@echo "GH_TOKEN: ${GH_TOKEN}" >> .env.yaml
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
	cloudtasks.googleapis.com \
	iamcredentials.googleapis.com

# Lets the deploy GitHub Actions workflow deploy the function via Workload
# Identity Federation, so no service account key is stored in GitHub. Prints
# the values for the GCP_WORKLOAD_IDENTITY_PROVIDER and GCP_SERVICE_ACCOUNT
# repository secrets.
github-deployer:
	@set -e; \
	project=$$(gcloud config get-value project); \
	number=$$(gcloud projects describe $$project --format='value(projectNumber)'); \
	sa=chefbot-deployer@$$project.iam.gserviceaccount.com; \
	pool=projects/$$number/locations/global/workloadIdentityPools/github; \
	gcloud iam service-accounts create chefbot-deployer --display-name="chefbot GitHub deployer"; \
	for role in roles/cloudfunctions.admin roles/run.admin; do \
		gcloud projects add-iam-policy-binding $$project --member=serviceAccount:$$sa --role=$$role --condition=None > /dev/null; \
	done; \
	gcloud iam service-accounts add-iam-policy-binding $$number-compute@developer.gserviceaccount.com \
		--member=serviceAccount:$$sa --role=roles/iam.serviceAccountUser > /dev/null; \
	gcloud iam workload-identity-pools create github --location=global --display-name=GitHub; \
	gcloud iam workload-identity-pools providers create-oidc recipes \
		--location=global \
		--workload-identity-pool=github \
		--issuer-uri=https://token.actions.githubusercontent.com \
		--attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository \
		--attribute-condition="assertion.repository=='rlucioni/recipes'"; \
	gcloud iam service-accounts add-iam-policy-binding $$sa \
		--role=roles/iam.workloadIdentityUser \
		--member=principalSet://iam.googleapis.com/$$pool/attribute.repository/rlucioni/recipes > /dev/null; \
	echo; \
	echo "GCP_WORKLOAD_IDENTITY_PROVIDER: $$pool/providers/recipes"; \
	echo "GCP_SERVICE_ACCOUNT: $$sa"

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
