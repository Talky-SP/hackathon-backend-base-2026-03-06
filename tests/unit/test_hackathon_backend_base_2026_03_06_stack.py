import aws_cdk as core
import aws_cdk.assertions as assertions

from hackathon_backend_base_2026_03_06.hackathon_backend_base_2026_03_06_stack import HackathonBackendBase20260306Stack

# example tests. To run these tests, uncomment this file along with the example
# resource in hackathon_backend_base_2026_03_06/hackathon_backend_base_2026_03_06_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = HackathonBackendBase20260306Stack(app, "hackathon-backend-base-2026-03-06")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
