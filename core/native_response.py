"""Metadata-only observation of an existing compatible chat stream.

No request options, permissions, retry rules, stored messages or research
ledger states are changed here. Provider termination and local delivery are
separate: a known finish can precede a failure while reading trailing usage.
"""
from __future__ import annotations

import re
import time
from .research_errors import classify_rejection, safe_request_id, safe_error_code


def _response_id(value):
    if safe_request_id(value):
        return safe_request_id(value)
    if isinstance(value,str) and re.fullmatch(r'chatcmpl-[A-Za-z0-9_-]{16,128}',value):
        return value
    return None


def _usage(value):
    if not isinstance(value,dict):
        return None
    result={}
    for key in ('prompt_tokens','completion_tokens','total_tokens'):
        count=value.get(key)
        if type(count) is int and count>=0:
            result[key]=count
    for key,allowed in (('prompt_tokens_details',('cached_tokens',)),
                        ('completion_tokens_details',('reasoning_tokens','accepted_prediction_tokens','rejected_prediction_tokens'))):
        details=value.get(key)
        if isinstance(details,dict):
            safe={k:details[k] for k in allowed if type(details.get(k)) is int and details[k]>=0}
            if safe: result[key]=safe
    return result or None


class NativeStreamReceipt:
    def __init__(self,attempt_id):
        self.attempt_id=attempt_id
        self.started=time.monotonic()
        self.response_ids=set()
        self.request_ids=set()
        self.unrecognised_response_id=False
        self.unrecognised_request_id=False
        self.finishes=set()
        self.unknown_finish=False
        self.http_status=None
        self.usage=None
        self.usage_status='NOT_REPORTED'
        self.delivery_status='RUNNING'
        self.failure_category=None
        self.rejection={}
        self.provider_error_code=None

    def headers(self,status,headers):
        self.http_status=status
        self.request_id(headers.get('x-request-id'))

    def request_id(self,value):
        request_id=safe_request_id(value)
        if request_id: self.request_ids.add(request_id)
        elif value is not None: self.unrecognised_request_id=True

    def rejection_body(self,body,headers):
        safe=classify_rejection(self.http_status,body,headers)
        request_id=safe.pop('provider_request_id',None)
        if request_id: self.request_ids.add(request_id)
        safe.pop('provider_request_id_status',None)
        self.rejection=safe
        if isinstance(body,dict):
            self.request_id(body.get('request_id'))
            if isinstance(body.get('error'),dict): self.request_id(body['error'].get('request_id'))

    def chunk(self,chunk):
        if not isinstance(chunk,dict):
            self.failure_category='STREAM_PROTOCOL_ERROR'
            return
        response_id=_response_id(chunk.get('id'))
        if response_id: self.response_ids.add(response_id)
        elif chunk.get('id') is not None: self.unrecognised_response_id=True
        self.request_id(chunk.get('request_id'))
        if chunk.get('usage') is not None:
            self.usage=_usage(chunk['usage'])
            self.usage_status='REPORTED' if self.usage else 'INVALID'
        error=chunk.get('error')
        if isinstance(error,dict):
            self.provider_error_code=safe_error_code(error.get('code')) or safe_error_code(error.get('type'))
            self.request_id(error.get('request_id'))
        choices=chunk.get('choices')
        for choice in choices if isinstance(choices,list) else []:
            reason=choice.get('finish_reason') if isinstance(choice,dict) else None
            if reason is not None:
                if reason in ('stop','tool_calls','length','content_filter','function_call'):
                    self.finishes.add(reason)
                else:
                    self.unknown_finish=True

    def result(self):
        if self.http_status in (400,401,403,404,422,429):
            status='FAILED'
        elif (self.http_status is not None and self.http_status>=400
              or len(self.response_ids)>1 or self.failure_category=='STREAM_PROVIDER_ERROR'
              or self.unknown_finish or len(self.finishes)!=1):
            status='UNKNOWN'
        else:
            reason=next(iter(self.finishes))
            status={'stop':'COMPLETE','tool_calls':'COMPLETE','length':'TRUNCATED',
                    'content_filter':'FAILED'}.get(reason,'UNKNOWN')
        result={'local_attempt_id':self.attempt_id,'result_status':status,
                'delivery_status':self.delivery_status,'http_status':self.http_status,
                'response_id':next(iter(self.response_ids)) if len(self.response_ids)==1 else None,
                'response_id_status':'REPORTED' if len(self.response_ids)==1 else 'CONFLICT' if self.response_ids else 'UNRECOGNISED' if self.unrecognised_response_id else 'NOT_REPORTED',
                'provider_request_id_status':'REPORTED' if len(self.request_ids)==1 else 'CONFLICT' if self.request_ids else 'UNRECOGNISED' if self.unrecognised_request_id else 'NOT_REPORTED',
                'finish_reason':next(iter(self.finishes)) if len(self.finishes)==1 and not self.unknown_finish else None,
                'finish_reason_status':'CONFLICT' if len(self.finishes)>1 else 'UNRECOGNISED' if self.unknown_finish else 'REPORTED' if self.finishes else 'NOT_REPORTED',
                'usage':self.usage,'usage_status':self.usage_status,
                'failure_category':self.failure_category,'elapsed_seconds':round(time.monotonic()-self.started,3),
                **self.rejection}
        if len(self.request_ids)==1: result['provider_request_id']=next(iter(self.request_ids))
        if self.provider_error_code: result['provider_error_code']=self.provider_error_code
        return result
