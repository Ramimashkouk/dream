import logging
import uuid
import re

from common.utils import get_named_persons
from common.personal_info import my_name_is_pattern

from flask import Flask, jsonify, request
from deeppavlov_kg import KnowledgeGraph, mocks

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)


# read all relations & properties to add them into ontology
rel_type_dict = {}
with open("rel_list.txt", "r") as fl:
    lines = fl.readlines()
    for line in lines:
        rel, rel_type = line.strip().split()
        if rel_type == "r":
            rel_type = "relation"
        else:
            rel_type = "property"
        rel_type_dict[rel.replace("_", " ")] = rel_type

graph = KnowledgeGraph(
    "bolt://neo4j:neo4j@neo4j:7687",
    ontology_kinds_hierarchy_path="deeppavlov_kg/database/ontology_kinds_hierarchy.pickle",
    ontology_data_model_path="deeppavlov_kg/database/ontology_data_model.json",
    db_ids_file_path="deeppavlov_kg/database/db_ids.txt"
)

# USE IF YOU WANT TO RESTART DATABASE
# graph.drop_database()
# mocks.populate(graph, drop=True)
# for rel in rel_type_dict:
#     if rel_type_dict[rel] == 'relation':
#         rel_name = '_'.join(rel.split(' ')).upper()
#         graph.ontology.create_relationship_kind(rel_name, "User")
#     else:
#         graph.ontology.create_entity_kind_properties("User", [rel])
# graph.ontology.create_entity_kind_properties("User", ["name"])


def add_name_property(graph, user_id, names):
    """Adds User Name property."""
    graph.create_or_update_property_of_entity(
        id_=user_id,
        property_kind="name",
        property_value=names[0],
    )
    logger.info(f"I already have you in the graph! Updating your property name to {names[0]}!")


def add_any_relationship(graph, entity_kind, entity_name, rel_type, user_id):
    """Creates an entity and a relation between it and the User from property extraction service."""
    entity_kind = entity_kind.replace('_', '').title()
    new_entity_id = str(uuid.uuid4())
    graph.ontology.create_entity_kind(entity_kind, kind_properties=["name"])
    graph.create_entity(entity_kind, new_entity_id, ["name"], [entity_name])
    graph.create_relationship(user_id, rel_type, new_entity_id)
    message = f'Added entity {entity_name} with Kind {entity_kind}! and connected it with the User {user_id}!' \
              f' {entity_name} is connected with User by {rel_type} relationship.'
    logger.info(message)


def add_any_property(graph, user_id, property_type, property_value):
    """Adds a property from property extraction service."""
    graph.create_or_update_property_of_entity(
        id_=user_id,
        property_kind=property_type,
        property_value=property_value,
    )
    logger.info(f"I added a property {property_type} with value {property_value}!")


def get_entity_type(attributes):
    """Extracts DBPedia type from property extraction annotator."""
    entity_info = attributes['entity_info']
    if not entity_info:
        return 'Misc'
    exact_entity_info = entity_info[list(entity_info.keys())[0]]
    finegrained = exact_entity_info.get('finegrained_types', [])
    if finegrained:
        entity_type = finegrained[0].capitalize()
        logger.info(f'Fine-grained type: {entity_type}')
        return entity_type
    return 'Misc'


def add_relations_or_properties(utt, user_id):
    """Chooses what to add: property, relationship or nothing."""
    no_rel_message = "No relations were found!"
    attributes = utt.get("annotations", {}).get("property_extraction", {})
    logger.info(f'Attributes: {attributes}')

    if attributes and attributes['triplet']:
        triplet = attributes['triplet']
        if triplet['subject'] != 'user':
            logger.info(no_rel_message)
            return {}
        if 'relation' in triplet:
            entity_kind = get_entity_type(attributes)
            entity_name = triplet['object']
            relation = '_'.join(triplet['relation'].split(' ')).upper()
            add_any_relationship(graph, entity_kind, entity_name, relation, user_id)
            return triplet
        else:
            add_any_property(graph, user_id, triplet['property'], triplet['object'])
            return triplet
    logger.info(no_rel_message)
    return {}


def name_scenario(utt, user_id):
    """Checks if there is a Name given and adds it as a property."""
    names = get_named_persons(utt)
    if not names:
        logger.info('No names were found.')
        return {}
    logger.info(f'I found a name: {names[0]}')
    existing_ids = [user[0].get("Id") for user in graph.search_for_entities("User")]
    if user_id not in existing_ids:
        # let's hope user is telling us their name if they're new here
        # actually that's an unreal situation -- delete this part
        add_name_property(graph, user_id, names)
        result = {'subject': 'user', 'property': 'name', 'object': names[0]}
    elif my_name_is_pattern.search(utt.get("text", "")):
        # if they're not new, search for pattern
        logger.info('I am in my name is patter if')
        add_name_property(graph, user_id, names)
        result = {'subject': 'user', 'property': 'name', 'object': names[0]}
    else:
        logger.info("You are telling me someone's name, but I guess it's not yours!")
        result = {}
    return result


def get_result(request):
    """Collects all relation & property information from one utterance and adds to graph."""
    uttrs = request.json.get("utterances", [])
    utt = uttrs[0]
    annotations = uttrs[0].get('annotations', {})
    logger.info(f"Text: {uttrs[0]['text']}")
    # logger.info(f"Entity detection: {annotations.get('entity_detection', {})}")
    # logger.info(f"Entity linking: {annotations.get('entity_linking', [])}")
    logger.info(f"Property Extraction: {annotations.get('property_extraction', [])}")

    last_utt = utt["text"]
    logger.info(f"Utterance: {last_utt}")
    if not last_utt:
        return "Empty utterance"

    user_id = str(utt.get("user", {}).get("id", ""))
    # graph.create_entity("User", user_id, ['name'], [])
    graph.create_entity("User", user_id, [], [])
    logger.info(f'Created User with id: {user_id}')

    entity_detection = utt.get("annotations", {}).get("entity_detection", [])
    entities = entity_detection.get('labelled_entities', [])
    entities = [entity.get('text', 'no entity name') for entity in entities]
    added = []
    name_result = {}
    if entities:
        name_result = name_scenario(utt, user_id)
    property_result = add_relations_or_properties(utt, user_id)
    if name_result:
        added.append(name_result)
    if property_result:
        added.append(property_result)
    return [{'added_to_graph': added}]


@app.route("/respond", methods=["POST"])
def respond():
    result = get_result(request)
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8127)
