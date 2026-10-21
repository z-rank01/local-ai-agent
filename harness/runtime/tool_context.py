"""Per-run discovery descriptions without changing tool schema or authority."""


def describe_tools(definitions, descriptions):
    return [{**definition, 'function': {**definition['function'],
             'description': descriptions.get(definition['function']['name'], definition['function'].get('description', ''))}}
            for definition in definitions]
