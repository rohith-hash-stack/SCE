# prism summarize: live run

Code `90ce565`, model `qwen2.5-coder:14b-instruct-q8_0`, Kaggle T4 x2.

| repo | functions | summarized | coverage | weight | lint any | gate | left | kill test | resume kept all | minutes |
|---|---|---|---|---|---|---|---|---|---|---|
| hono | 555 | 555 | 1.0 | 0.5 | 0.005 | pass | 0 | killed, 120 on disk, valid True | True | 7.9 |

## hono

```
{
 "lint": {
  "axis_label": 0.0,
  "filler": 0.0,
  "identifier": 0.005,
  "callee_list": 0.0,
  "any": 0.005,
  "n": 555
 },
 "asks": []
}
```

Sample summaries:

- `adapter.aws-lambda.handler.ALBProcessor.getCookies`: Processes cookies from an Application Load Balancer event and headers.
- `adapter.aws-lambda.handler.ALBProcessor.getHeaders`: Retrieves headers from an Application Load Balancer event.
- `adapter.aws-lambda.handler.ALBProcessor.getMethod`: Gets the HTTP method from an Application Load Balancer event.
- `adapter.aws-lambda.handler.ALBProcessor.getPath`: Extracts the path from an Application Load Balancer event.
- `adapter.aws-lambda.handler.ALBProcessor.getQueryString`: Fetches the query string from an Application Load Balancer event.
- `adapter.aws-lambda.handler.ALBProcessor.setCookiesToResult`: Sets cookies in the API Gateway result by joining string array.
- `adapter.aws-lambda.handler.EventV1Processor.getCookies`: Processes cookies from a non-API Gateway V2 Lambda event and headers.
- `adapter.aws-lambda.handler.EventV1Processor.getHeaders`: Extracts headers from an API Gateway V1 event.
- `adapter.aws-lambda.handler.EventV1Processor.getMethod`: Fetches the HTTP method from a non-API Gateway V2 Lambda event.
- `adapter.aws-lambda.handler.EventV1Processor.getPath`: Retrieves the path from a non-API Gateway V2 Lambda event.
- `adapter.aws-lambda.handler.EventV1Processor.getQueryString`: Gets the query string from a non-API Gateway V2 Lambda event.
- `adapter.aws-lambda.handler.EventV1Processor.setCookiesToResult`: Adds cookies to an API Gateway proxy result.
