export const meta = {
  "name": "dual-review",
  "description": "Two blind session delegate reviews returned for Python grounding and self reconciliation.",
  "phases": [
    "Blind first passes"
  ]
};

const inputSchema = {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "$id": "https://autofusion.dev/schemas/workflow-dual-review-input.schema.json",
  "required": [
    "schema_version",
    "run_id",
    "topology",
    "requests"
  ],
  "properties": {
    "schema_version": {
      "const": "1"
    },
    "run_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 128
    },
    "topology": {
      "const": "dual-review"
    },
    "requests": {
      "type": "array",
      "minItems": 2,
      "maxItems": 2,
      "items": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": false,
        "$id": "https://autofusion.dev/schemas/session-request.schema.json",
        "title": "Bounded active-session delegate request",
        "required": [
          "schema_version",
          "run_id",
          "call_id",
          "packet_hash",
          "artifact_manifest_hash",
          "reviewer_schema_id",
          "reviewer_schema_hash",
          "packet",
          "artifact_manifest",
          "reviewer_schema",
          "role",
          "dispatchable",
          "requested_identity",
          "prompt",
          "max_output_chars"
        ],
        "properties": {
          "schema_version": {
            "const": "1"
          },
          "run_id": {
            "type": "string",
            "pattern": "^run-[A-Za-z0-9-]+$",
            "maxLength": 128
          },
          "call_id": {
            "type": "string",
            "pattern": "^call-[a-f0-9]{16}$"
          },
          "packet_hash": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$"
          },
          "artifact_manifest_hash": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$"
          },
          "reviewer_schema_id": {
            "const": "https://autofusion.dev/schemas/reviewer-output.schema.json"
          },
          "reviewer_schema_hash": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$"
          },
          "packet": {
            "type": "object",
            "required": [
              "snapshot"
            ]
          },
          "artifact_manifest": {
            "type": "object",
            "required": [
              "snapshot_hash",
              "manifest_hash",
              "entries"
            ],
            "additionalProperties": false,
            "properties": {
              "snapshot_hash": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$"
              },
              "manifest_hash": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$"
              },
              "entries": {
                "type": "array",
                "items": {
                  "type": "object"
                }
              }
            }
          },
          "reviewer_schema": {
            "type": "object"
          },
          "role": {
            "const": "reviewer"
          },
          "dispatchable": {
            "type": "boolean"
          },
          "requested_identity": {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "model",
              "vendor",
              "family",
              "delegate",
              "handle"
            ],
            "properties": {
              "model": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256
              },
              "vendor": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256
              },
              "family": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256
              },
              "delegate": {
                "enum": [
                  "sol-delege",
                  "astra-delege",
                  "terra-delege",
                  "gemini-delege",
                  "grok-delege",
                  null
                ]
              },
              "handle": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256
              }
            }
          },
          "prompt": {
            "type": "string",
            "minLength": 1,
            "maxLength": 1048576
          },
          "max_output_chars": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1048576
          }
        }
      }
    }
  }
};

const outputSchema = {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "$id": "https://autofusion.dev/schemas/workflow-dual-review-output.schema.json",
  "required": [
    "schema_version",
    "run_id",
    "topology",
    "results"
  ],
  "properties": {
    "schema_version": {
      "const": "1"
    },
    "run_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 128
    },
    "topology": {
      "const": "dual-review"
    },
    "results": {
      "type": "array",
      "minItems": 2,
      "maxItems": 2,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "call_id",
          "result"
        ],
        "properties": {
          "call_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128
          },
          "result": {
            "anyOf": [
              {
                "type": "null"
              },
              {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": false,
                "$id": "https://autofusion.dev/schemas/session-result.schema.json",
                "title": "Correlated active-session delegate result",
                "required": [
                  "schema_version",
                  "run_id",
                  "call_id",
                  "packet_hash",
                  "artifact_manifest_hash",
                  "reviewer_schema_id",
                  "reviewer_schema_hash",
                  "status",
                  "actual_identity",
                  "output",
                  "error"
                ],
                "properties": {
                  "schema_version": {
                    "const": "1"
                  },
                  "run_id": {
                    "type": "string",
                    "pattern": "^run-[A-Za-z0-9-]+$",
                    "maxLength": 128
                  },
                  "call_id": {
                    "type": "string",
                    "pattern": "^call-[a-f0-9]{16}$"
                  },
                  "packet_hash": {
                    "type": "string",
                    "pattern": "^[a-f0-9]{64}$"
                  },
                  "artifact_manifest_hash": {
                    "type": "string",
                    "pattern": "^[a-f0-9]{64}$"
                  },
                  "reviewer_schema_id": {
                    "const": "https://autofusion.dev/schemas/reviewer-output.schema.json"
                  },
                  "reviewer_schema_hash": {
                    "type": "string",
                    "pattern": "^[a-f0-9]{64}$"
                  },
                  "status": {
                    "enum": [
                      "completed",
                      "missing",
                      "failed",
                      "cancelled",
                      "malformed",
                      "abstaining"
                    ]
                  },
                  "actual_identity": {
                    "anyOf": [
                      {
                        "type": "object",
                        "additionalProperties": false,
                        "required": [
                          "model",
                          "vendor",
                          "family",
                          "delegate"
                        ],
                        "properties": {
                          "model": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 256
                          },
                          "vendor": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 256
                          },
                          "family": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 256
                          },
                          "delegate": {
                            "enum": [
                              "sol-delege",
                              "astra-delege",
                              "terra-delege",
                              "gemini-delege",
                              "grok-delege"
                            ]
                          }
                        }
                      },
                      {
                        "type": "null"
                      }
                    ]
                  },
                  "output": {},
                  "error": {
                    "type": [
                      "string",
                      "null"
                    ],
                    "maxLength": 4096
                  }
                }
              }
            ]
          }
        }
      }
    }
  }
};


// Source: https://code.claude.com/docs/en/workflows#edit-a-saved-script
// agent(prompt, {agentType, schema}) and parallel(thunks) are also specified
// by the bundled workflow-authoring reference. Live Workflow execution: NOT_RUN.
// Python validates inputSchema before dispatch and outputSchema before import.
const request = args;
const correlation = ["schema_version", "run_id", "call_id", "packet_hash",
  "artifact_manifest_hash", "reviewer_schema_id", "reviewer_schema_hash"];
const delegates = {
  "sol-delege": "gpt-5.6-sol", "astra-delege": "gpt-6-astra",
  "terra-delege": "gpt-5.6-terra", "gemini-delege": "gemini-3.8-flash-high",
  "grok-delege": "grok-4.6"
};
if (!request || request.schema_version !== "1" || request.topology !== "dual-review"
    || !Array.isArray(request.requests) || request.requests.length !== 2) {
  throw new Error("Expected a Python-prepared dual-review request");
}
const [left, right] = request.requests;
for (const call of request.requests) {
  const identity = call.requested_identity;
  if (!identity || identity.handle === "self"
      || /fable/i.test(JSON.stringify(identity))
      || call.run_id !== request.run_id || call.role !== "reviewer"
      || typeof call.prompt !== "string" || !call.reviewer_schema
      || typeof call.dispatchable !== "boolean"
      || call.dispatchable !== (identity.delegate !== null)
      || (call.dispatchable && delegates[identity.delegate] !== identity.model)) {
    throw new Error("Invalid or prohibited delegate request");
  }
}
if (left.call_id === right.call_id
    || left.requested_identity.model === right.requested_identity.model
    || left.packet_hash !== right.packet_hash
    || left.artifact_manifest_hash !== right.artifact_manifest_hash
    || left.reviewer_schema_hash !== right.reviewer_schema_hash
    || JSON.stringify(left.packet) !== JSON.stringify(right.packet)
    || JSON.stringify(left.artifact_manifest) !== JSON.stringify(right.artifact_manifest)
    || JSON.stringify(left.reviewer_schema) !== JSON.stringify(right.reviewer_schema)) {
  throw new Error("Blind reviewers require distinct calls and the same frozen packet");
}
phase("Blind first passes");
const outputs = await parallel(request.requests.map(call => async () => {
  if (!call.dispatchable) return null;
  const bound = Object.fromEntries(correlation.map(key => [key, call[key]]));
  const schema = JSON.parse(JSON.stringify(
    outputSchema.properties.results.items.properties.result.anyOf[1]
  ));
  for (const key of correlation) schema.properties[key] = { const: call[key] };
  schema.properties.output = { anyOf: [{ type: "null" }, call.reviewer_schema] };
  // The delegate registry selects the route. No model override is supplied here.
  // Requested identity is never copied into actual_identity by this script.
  const prompt = call.prompt + "\nReturn a session result envelope with these correlation fields: "
    + JSON.stringify(bound)
    + "\nPopulate actual_identity only from this invocation's actual model identity. "
    + "Never infer it from the requested route. If unavailable return failed with "
    + "actual_identity:null, output:null and an explanation. Return the review in output. "
    + "Only your final structured output reaches the caller; intermediate messages do not. "
    + "Do not edit files, execute commands, contact peers, or reconcile the artifact. "
    + "Requested route (not identity evidence): " + JSON.stringify(call.requested_identity);
  const result = await agent(prompt, {
    agentType: call.requested_identity.delegate, schema
  });
  // A stopped or failed agent stays null, attached to its original call below.
  if (result === null) return null;
  if (typeof result !== "object" || Array.isArray(result)
      || correlation.some(key => result[key] !== call[key])) {
    throw new Error("Agent returned an invalid correlated result");
  }
  return result;
}));
return {
  schema_version: "1", run_id: request.run_id, topology: "dual-review",
  results: request.requests.map((call, index) => ({ call_id: call.call_id, result: outputs[index] }))
};
