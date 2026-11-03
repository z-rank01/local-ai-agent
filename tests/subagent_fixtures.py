from dataclasses import asdict
from harness.contracts.subagents import Artifact, RoleResult, RoleSpec


class MemoryStore:
    def __init__(self):
        self.batches = {}

    def create_batch(self, batch_id, tasks):
        self.batches[batch_id] = {'role_ids':[task.role.role_id for task in tasks], 'results':{}}

    def save_result(self, batch_id, result):
        self.batches[batch_id]['results'][result.role_id] = {
            **asdict(result), 'result_ref':'mem://' + result.role_id}

    def get_batch(self, batch_id):
        return {'batch_id':batch_id, **self.batches[batch_id]}


def role(role_id, *, tools=(), depends_on=(), kind='analysis'):
    return RoleSpec(role_id=role_id, prompt='按任务返回结构化成果', tools=frozenset(tools),
        depends_on=tuple(depends_on), output_artifact_kind=kind, recipe_namespace=kind)


def complete_result(role_id, content='ok'):
    return RoleResult(role_id=role_id, status='COMPLETE', artifacts=[
        Artifact(kind='analysis', content=content, producer_role=role_id, sha256='a'*64)])
