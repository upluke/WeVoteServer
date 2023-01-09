import json
import os

from django.core.management.base import BaseCommand

from config.base import get_environment_variable

# For testing the job queue system locally, you can run an SQS 
# server locally using localstack within docker.
# Use the following commands to install:
#
#   pip install localstack localstack-client awscli-local
#
#  If 'docker' cli is not available at the command line...
#    Get the docker CLI at https://docs.docker.com/desktop/install/mac-install/
#    Find the downloaded file, and substitute its path in the following set of commands
#      (venv2) WeVoteServer % sudo hdiutil attach '/Users/stevepodell/Downloads/Docker (1).dmg'
#      (venv2) WeVoteServer % sudo /Volumes/Docker/Docker.app/Contents/MacOS/install
#      (venv2) WeVoteServer % sudo hdiutil detach /Volumes/Docker
#    In a MacOS modal dialog that appears, allow docker to make some symbolic links
#    Once the Docker Desktop starts, and shows as running, typing 'docker' at the command line, will show a response
#
# if aws (awslocal) is not available at the command line...
#   Follow instructions at
#   https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html#getting-started-install-instructions
#
# Start the aws local sqs service
#    localstack start -d     (wait for sqs service to launch)
#
# Create a sqs queue, and copy the QueueUrl it reports to environment-variables.json
#   awslocal sqs create-queue --queue-name job-queue.fifo --attributes FifoQueue=true,ContentBasedDeduplication=true
#
# Make sure the QueueUrl displayed matches AWS_SQS_WEB_QUEUE_URL in
#  config file environment-variables.json

# max time (in sec) that a job may take to complete
#  this prevents a different worker from picking up a job that
#  is currently being handled by another worker
MAX_JOB_PROCESSING_TIME = 60
MAX_JOB_RETRY_ATTEMPTS = 5

def process_request(function, body, message):

    if function == 'caching_facebook_images_for_retrieve_process':
        from import_export_facebook.controllers import caching_facebook_images_for_retrieve_process
        repair_facebook_related_voter_caching_now = body['repair_facebook_related_voter_caching_now']
        facebook_auth_response = body['facebook_auth_response']
        voter_we_vote_id_attached_to_facebook = body['voter_we_vote_id_attached_to_facebook']
        voter_we_vote_id_attached_to_facebook_email = body['voter_we_vote_id_attached_to_facebook_email']
        voter_we_vote_id = body['voter_we_vote_id']

        # print("caching_facebook_images_for_retrieve_process from SQS in a Lambda: %s, %s %s, %s, " %
        #       (voter_we_vote_id, facebook_auth_response.facebook_first_name, facebook_auth_response.facebook_last_name,
        #        facebook_auth_response.facebook_email))
        caching_facebook_images_for_retrieve_process(repair_facebook_related_voter_caching_now,
                                                     facebook_auth_response,
                                                     voter_we_vote_id_attached_to_facebook,
                                                     voter_we_vote_id_attached_to_facebook_email,
                                                     voter_we_vote_id)
    elif function == 'voter_cache_facebook_images_process':
        from voter.controllers import voter_cache_facebook_images_process
        voter = body['voter']
        facebook_auth_response = body['facebook_auth_response']
        print("voter_cache_facebook_images_process from SQS in a Lambda: %s, %s %s, %s, " %
              (voter.we_vote_id, facebook_auth_response.facebook_first_name, facebook_auth_response.facebook_last_name,
               facebook_auth_response.facebook_email))
        voter_cache_facebook_images_process(voter, facebook_auth_response)
    else:
        # default: no function found, act as
        #  processed, so it gets deleted
        print(f"Job references unknown function [{function}], deleting.")

    return True


def worker_run(queue_url):
    if queue_url.startswith('http://localhost'):
        try:
            import localstack_client.session as boto3
        except:
            import boto3
    else:
        import boto3

    sqs = boto3.client('sqs')

    while True:
        # Receive message from SQS queue
        response = sqs.receive_message(
            QueueUrl=queue_url,
            AttributeNames=['All'],
            MaxNumberOfMessages=1,
            MessageAttributeNames=['All'],
            VisibilityTimeout=MAX_JOB_PROCESSING_TIME,
            WaitTimeSeconds=20
        )

        if 'Messages' in response.keys() and len(response['Messages']) > 0:
            message = response['Messages'][0]
            print("Got message:", message)
            receipt_handle = message['ReceiptHandle']
            processed = False


            if 'Function' in message['MessageAttributes'].keys():
                function = message['MessageAttributes']['Function']['StringValue']
                print(f"Calling function [{function}]")
                body = json.loads(message['Body'])
                try:
                    processed = process_request(function, body, message)
                except Exception as e:
                    print("Failed to call function {function}:", e)

            else:
                print("No function provided in SQS message, deleting invalid request.")
                processed = True

            # expire messages after max number of retries
            if not processed:
                job_retry_count = int(message['Attributes']['ApproximateReceiveCount'])
                if job_retry_count > MAX_JOB_RETRY_ATTEMPTS:
                    print("Message crossed max retry attempts, deleting.")
                    processed = True

            # Delete processed message from queue
            if processed:
                sqs.delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=receipt_handle
                )
                print('Deleted message: %s' % message)



class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        print("Starting job worker, waiting for jobs from SQS..")
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        sqs_url = get_environment_variable("AWS_SQS_WEB_QUEUE_URL")
        worker_run(sqs_url)




    
