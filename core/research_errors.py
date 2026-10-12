"""Fixed rejection labels and UUID trace references; provider prose is never stored."""
from __future__ import annotations

import re


ERROR_CODES = {
    'InvalidParameter': 'PARAMETER_INVALID',
    'InternalError.Algo.InvalidParameter': 'PARAMETER_INVALID',
    'context_length_exceeded': 'CONTEXT_LENGTH_EXCEEDED',
    'unsupported_parameter': 'UNSUPPORTED_PARAMETER',
    'DataInspectionFailed': 'CONTENT_REJECTED',
    'data_inspection_failed': 'CONTENT_REJECTED',
    'InvalidApiKey': 'AUTHENTICATION',
    'invalid_api_key': 'AUTHENTICATION',
    'AccessDenied': 'ACCESS_DENIED',
    'Arrearage': 'BILLING',
    'Throttling': 'RATE_LIMIT',
    'Throttling.RateQuota': 'RATE_LIMIT',
    'model_not_found': 'ENDPOINT_OR_MODEL_UNAVAILABLE',
}
UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')


def safe_error_code(value):
    if not isinstance(value, str):
        return None
    return next((code for code in ERROR_CODES if value.casefold() == code.casefold()), None)


def safe_request_id(value):
    return value.lower() if isinstance(value, str) and UUID.fullmatch(value) else None


def classify_rejection(status, body, headers):
    category = {401: 'AUTHENTICATION', 403: 'ACCESS_DENIED',
                404: 'ENDPOINT_OR_MODEL_UNAVAILABLE', 429: 'RATE_LIMIT'}.get(status, 'UNCLASSIFIED')
    body = body if isinstance(body, dict) else {}
    error = body.get('error', body)
    error = error if isinstance(error, dict) else {}
    code = safe_error_code(error.get('code')) or safe_error_code(error.get('type'))
    if code and (status in (400, 422) or category == 'UNCLASSIFIED'
                 or status == 403 and ERROR_CODES[code] in ('BILLING', 'CONTENT_REJECTED', 'ACCESS_DENIED')):
        category = ERROR_CODES[code]
    message = error.get('message')
    message = message.strip().lower() if isinstance(message, str) else ''
    # Recognise the provider's error sentence, not matching text inside echoed input.
    if status in (400, 422) and category in ('UNCLASSIFIED', 'PARAMETER_INVALID'):
        if message.startswith(('maximum context length', "this model's maximum context length", 'input length exceeds',
                'input token count exceeds', 'input is too long', 'range of input length should be',
                'total message token length exceed model limit')):
            category = 'CONTEXT_LENGTH_EXCEEDED'
        elif message.startswith(('unsupported parameter', 'unrecognized request argument')):
            category = 'UNSUPPORTED_PARAMETER'
        elif message.startswith(('range of max_tokens should be', 'the thinking_budget parameter must be',
                'temperature should be in', 'range of top_p should be')):
            category = 'PARAMETER_OUT_OF_RANGE'
        elif message.startswith(('parameter.enable_thinking must be set to false for non-streaming calls',
                'parameter.enable_thinking only support stream call', 'this model only support stream mode',
                'the incremental_output parameter must be "true" when enable_thinking is true')):
            category = 'PARAMETER_COMBINATION_INVALID'
    result = {'http_status': status, 'rejection_category': category}
    if code:
        result['provider_error_code'] = code
    refs = {ref for value in (body.get('request_id'), error.get('request_id'), headers.get('x-request-id'))
            if (ref := safe_request_id(value))}
    result['provider_request_id_status'] = 'REPORTED' if len(refs) == 1 else 'CONFLICT' if refs else 'NOT_REPORTED'
    if len(refs) == 1:
        result['provider_request_id'] = refs.pop()
    return result
