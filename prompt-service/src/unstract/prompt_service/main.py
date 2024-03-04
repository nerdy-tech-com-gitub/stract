import json
import logging
import re
import sqlite3
from typing import Any, Optional

import nltk
import peewee
from dotenv import load_dotenv
from flask import Flask, request
from llama_index import (
    QueryBundle,
    ServiceContext,
    VectorStoreIndex,
    get_response_synthesizer,
)
from llama_index.indices.base_retriever import BaseRetriever
from llama_index.llms import LLM
from llama_index.query_engine import (
    RetrieverQueryEngine,
    SubQuestionQueryEngine,
)
from llama_index.schema import NodeWithScore
from llama_index.tools import QueryEngineTool, ToolMetadata
from llama_index.vector_stores.types import (
    ExactMatchFilter,
    MetadataFilters,
    VectorStore,
    VectorStoreQuery,
    VectorStoreQueryResult,
)
from nltk import ngrams
from unstract.prompt_service.authentication_middleware import (
    AuthenticationMiddleware,
)
from unstract.prompt_service.constants import PromptServiceContants as PSKeys
from unstract.prompt_service.constants import Query
from unstract.prompt_service.helper import PromptServiceHelper, plugin_loader
from unstract.prompt_service.prompt_ide_base_tool import PromptServiceBaseTool
from unstract.sdk.constants import LogLevel
from unstract.sdk.embedding import ToolEmbedding
from unstract.sdk.index import ToolIndex
from unstract.sdk.llm import ToolLLM
from unstract.sdk.tool.base import BaseTool
from unstract.sdk.utils.service_context import (
    ServiceContext as UNServiceContext,
)
from unstract.sdk.vector_db import ToolVectorDB

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s : %(message)s",
)
MAX_RETRIES = 3

db_name = "unstract_vector_db"
POS_TEXT_PATH = "/tmp/pos.txt"
USE_UNSTRACT_PROMPT = True

PG_BE_HOST = PromptServiceHelper.get_env_or_die("PG_BE_HOST")
PG_BE_PORT = PromptServiceHelper.get_env_or_die("PG_BE_PORT")
PG_BE_USERNAME = PromptServiceHelper.get_env_or_die("PG_BE_USERNAME")
PG_BE_PASSWORD = PromptServiceHelper.get_env_or_die("PG_BE_PASSWORD")
PG_BE_DATABASE = PromptServiceHelper.get_env_or_die("PG_BE_DATABASE")

be_db = peewee.PostgresqlDatabase(
    PG_BE_DATABASE,
    user=PG_BE_USERNAME,
    password=PG_BE_PASSWORD,
    host=PG_BE_HOST,
    port=PG_BE_PORT,
)
be_db.init(PG_BE_DATABASE)
be_db.connect()

AuthenticationMiddleware.be_db = be_db

app = Flask("prompt-service")

plugins = plugin_loader()


def get_keywords_from_pos(text: str) -> list[Any]:
    text = text.lower()
    keywords = []
    sentences = nltk.sent_tokenize(text)

    words_allowed_only_in_middle = [PSKeys.AND, PSKeys.TO, PSKeys.OR, PSKeys.IS]
    pos_lookup: dict[str, Any] = {
        "NN": [],
        "VB": [],
        "JJ": [],
        "IN": [],
        "DT": [],
        ".": [],
        "X": [],
        "PRP": [],
        "RB": [],
        "EX": [],
        "WDT": [],
        "WP": [],
        "MD": [],
    }
    for sentence in sentences:
        # TODO : Revisit pos.txt -> non generic usecase
        with open(POS_TEXT_PATH, "w") as f:
            f.write("***********\n")
        pos = nltk.pos_tag(nltk.word_tokenize(str(sentence)))

        for word, posx in pos:
            if posx.startswith("NN"):
                posx = "NN"
            if posx.startswith("VB"):
                posx = "VB"
            if posx.endswith("$"):
                posx = "X"
            if posx.startswith("RB"):
                posx = "RB"
            if posx not in pos_lookup:
                pos_lookup[posx] = []
            pos_lookup[posx].append(word)
        # with open("samples/pos.txt", "a") as f:
        #     f.write(str(pos_lookup) + "\n")
        words = nltk.word_tokenize(sentence)
        trigrams = list(ngrams(words, 3))
        for trigram in trigrams:
            allowed = False
            override_allowed = False
            p = 0
            for word in trigram:
                if (
                    word in pos_lookup["NN"]
                    or word in pos_lookup["VB"]
                    or word in pos_lookup["JJ"]
                ):
                    allowed = True
                if (
                    word in pos_lookup["IN"]
                    or word in pos_lookup["DT"]
                    or word in pos_lookup["."]
                    or word in pos_lookup["X"]
                    or word in pos_lookup["PRP"]
                    or word in pos_lookup["RB"]
                    or word in pos_lookup["EX"]
                    or word in pos_lookup["WDT"]
                    or word in pos_lookup["WP"]
                    or word in pos_lookup["MD"]
                    or word in PSKeys.disallowed_words
                ):
                    override_allowed = True
                if p == 0 or p == 2:
                    if word in words_allowed_only_in_middle:
                        override_allowed = True
                p += 1
            if allowed and not override_allowed:
                keywords.append(" ".join(trigram))
        bigrams = list(ngrams(words, 2))
        for bigram in bigrams:
            allowed = False
            override_allowed = False
            for word in bigram:
                if (
                    word in pos_lookup["NN"]
                    or word in pos_lookup["VB"]
                    or word in pos_lookup["JJ"]
                ):
                    allowed = True
                if (
                    word in pos_lookup["IN"]
                    or word in pos_lookup["DT"]
                    or word in pos_lookup["."]
                    or word in pos_lookup["X"]
                    or word in pos_lookup["PRP"]
                    or word in pos_lookup["RB"]
                    or word in pos_lookup["EX"]
                    or word in pos_lookup["WDT"]
                    or word in pos_lookup["WP"]
                    or word in pos_lookup["MD"]
                    or word in PSKeys.disallowed_words
                ):
                    override_allowed = True
                # In bigrams, these words cannot be in the middle
                # so if they are preset, remove the bigram
                if word in words_allowed_only_in_middle:
                    override_allowed = True

            if allowed and not override_allowed:
                keywords.append(" ".join(bigram))
    with open(POS_TEXT_PATH, "a") as f:
        f.write(str(keywords) + "\n")
    return keywords


class UnstractRetriever_V_K(BaseRetriever):
    def __init__(
        self,
        index: VectorStoreIndex,
        doc_id: str,
        vector_db: VectorStore,
        collection: str,
        service_context: ServiceContext,
        tool: BaseTool,
    ):
        self.index = index
        self.db_name = f"/tmp/{doc_id}.db"
        self.service_context = service_context
        self.doc_id = doc_id
        self.collection = collection
        self.vector_db = vector_db
        self.tool = tool

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        print(f"Query: {query_bundle.query_str}")
        # vec_retriever = self.index.as_retriever(
        #     similarity_top_k=2,
        #     filters=MetadataFilters(
        #         filters=[
        #             ExactMatchFilter(key=PSKeys.DOC_ID, value=self.doc_id)
        #         ],
        #     ),
        # )
        keywords = get_keywords_from_pos(query_bundle.query_str)
        print(f"Keywords: {keywords}")

        db = sqlite3.connect(self.db_name)
        cursor = db.cursor()
        cursor.execute(Query.DROP_TABLE)
        db.commit()
        cursor.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS nodes "
            "USING fts5(doc_id, node_id, text, tokenize='porter unicode61');"
        )
        db.commit()

        try:
            embedding_li = self.service_context.embed_model
            q = VectorStoreQuery(
                query_embedding=embedding_li.get_query_embedding(" "),
                doc_ids=[self.doc_id],
                similarity_top_k=10000,
            )
        except Exception as e:
            self.tool.stream_log(f"Error creating querying : {e}")
            raise Exception(f"Error creating querying : {e}")

        n: VectorStoreQueryResult = self.vector_db.query(query=q)
        # all_nodes = n.nodes
        all_nodes = []
        for node in n.nodes:  # type:ignore
            all_nodes.append(NodeWithScore(node=node, score=0.8))

        if len(n.nodes) > 0:  # type:ignore
            for node in n.nodes:  # type:ignore
                node_chunk_text = re.sub(" +", " ", node.get_content()).replace(
                    "\n", " "
                )
                node_text = node_chunk_text
                cursor.execute(
                    Query.INSERT_INTO,
                    (self.doc_id, node.node_id, node_text),
                )
            db.commit()
        else:
            self.tool.stream_log(f"No nodes found for {self.doc_id}")

        keyword_nodes_metadata = get_nodes_with_keywords(self.db_name, keywords)
        keyword_node_ids = []
        for node in keyword_nodes_metadata:
            keyword_node_ids.append(node[1])

        # Get node from index using node_id
        keyword_nodes = []

        for node in all_nodes:
            if node.node_id in keyword_node_ids:
                keyword_nodes.append(node)
                # print(node)

        return keyword_nodes
        # return keyword_nodes + vec_nodes
        # return vec_nodes


def construct_prompt(
    preamble: str,
    prompt: str,
    postamble: str,
    grammar_list: list[dict[str, Any]],
    context: str,
) -> str:
    # Let's cleanup the context. Remove if 3 consecutive newlines are found
    context_lines = context.split("\n")
    new_context_lines = []
    empty_line_count = 0
    for line in context_lines:
        if line.strip() == "":
            empty_line_count += 1
        else:
            if empty_line_count >= 3:
                empty_line_count = 3
            for i in range(empty_line_count):
                new_context_lines.append("")
            empty_line_count = 0
            new_context_lines.append(line.rstrip())
    context = "\n".join(new_context_lines)
    app.logger.info(
        f"Old context length: {len(context_lines)}, "
        f"New context length: {len(new_context_lines)}"
    )

    prompt = (
        f"{preamble}\n\nContext:\n---------------{context}\n"
        f"-----------------\n\nQuestion or Instruction: {prompt}\n"
    )
    if grammar_list is not None and len(grammar_list) > 0:
        prompt += "\n"
        for grammar in grammar_list:
            word = ""
            synonyms = []
            if PSKeys.WORD in grammar:
                word = grammar[PSKeys.WORD]
                if PSKeys.SYNONYMS in grammar:
                    synonyms = grammar[PSKeys.SYNONYMS]
            if len(synonyms) > 0 and word != "":
                prompt += f'\nNote: You can consider that the word {word} is same as \
                    {", ".join(synonyms)} in both the quesiton and the context.'  # noqa
    prompt += f"\n\n{postamble}"
    prompt += "\n\nAnswer:"
    return prompt


def construct_prompt_for_engine(
    preamble: str,
    prompt: str,
    postamble: str,
    grammar_list: list[dict[str, Any]],
) -> str:
    # Let's cleanup the context. Remove if 3 consecutive newlines are found

    prompt = f"{preamble}\n\nQuestion or Instruction: {prompt}\n"
    if grammar_list is not None and len(grammar_list) > 0:
        prompt += "\n"
        for grammar in grammar_list:
            word = ""
            synonyms = []
            if PSKeys.WORD in grammar:
                word = grammar[PSKeys.WORD]
                if PSKeys.SYNONYMS in grammar:
                    synonyms = grammar[PSKeys.SYNONYMS]
            if len(synonyms) > 0 and word != "":
                prompt += f'\nNote: You can consider that the word {word} is same as \
                    {", ".join(synonyms)} in both the quesiton and the context.'  # noqa
    prompt += f"\n\n{postamble}"
    prompt += "\n\n"
    return prompt


def get_nodes_with_keywords(db_name: str, keywords: list[Any]) -> list[Any]:
    if len(keywords) == 0:
        return []
    db = sqlite3.connect(db_name)
    cursor = db.cursor()
    keywords = ['"' + k + '"' for k in keywords]
    # keywords = ['dosing schedules']
    query = Query.SELECT
    for i in range(len(keywords)):
        if i == 0:
            query += Query.NODE_MATCH
        else:
            query += " OR " + Query.NODE_MATCH
    query += Query.ORDER_BY
    res = cursor.execute(query, keywords)
    nodes = []
    for r in res:
        nodes.append(r)
    return nodes


def authentication_middleware(func: Any) -> Any:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = AuthenticationMiddleware.get_token_from_auth_header(request)
        # Check if bearer token exists and validate it
        if not token or not AuthenticationMiddleware.validate_bearer_token(
            token
        ):
            return "Unauthorized", 401

        return func(*args, **kwargs)

    return wrapper


@app.route("/answer-prompt", methods=["POST", "GET", "DELETE"])
@authentication_middleware
def prompt_processor() -> Any:
    result: dict[str, Any] = {}
    platform_key = AuthenticationMiddleware.get_token_from_auth_header(request)
    if request.method == "POST":
        payload: dict[Any, Any] = request.json
        if not payload:
            result["error"] = "Bad Request / No payload"
            return result, 400
    outputs = payload.get(PSKeys.OUTPUTS)
    tool_id = payload.get(PSKeys.TOOL_ID)
    file_hash = payload.get(PSKeys.FILE_HASH)
    structured_output: dict[str, Any] = {}
    variable_names: list[str] = []

    for output in outputs:  # type:ignore
        variable_names.append(output[PSKeys.NAME])
    for output in outputs:  # type:ignore
        active = output[PSKeys.ACTIVE]
        name = output[PSKeys.NAME]
        promptx = output[PSKeys.PROMPT]
        chunk_size = output[PSKeys.CHUNK_SIZE]
        util = PromptServiceBaseTool(
            log_level=LogLevel.INFO, platform_key=platform_key
        )
        tool_index = ToolIndex(tool=util)

        app.logger.info(f"Processing output for : {name}")

        if active is False:
            app.logger.info(f"Output {name} is not active. Skipping")
            continue

        # Finding and replacing the variables in the prompt
        # The variables are in the form %variable_name%

        output[PSKeys.PROMPTX] = extract_variable(
            structured_output, variable_names, output, promptx
        )

        doc_id = ToolIndex.generate_file_id(
            tool_id=tool_id,
            file_hash=file_hash,
            vector_db=output[PSKeys.VECTOR_DB],
            embedding=output[PSKeys.EMBEDDING],
            x2text=output[PSKeys.X2TEXT_ADAPTER],
            chunk_size=output[PSKeys.CHUNK_SIZE],
            chunk_overlap=output[PSKeys.CHUNK_OVERLAP],
        )

        llm_helper = ToolLLM(tool=util)
        llm_li: Optional[LLM] = llm_helper.get_llm(
            adapter_instance_id=output[PSKeys.LLM]
        )
        if llm_li is None:
            msg = f"Couldn't fetch LLM {output[PSKeys.LLM]}"
            app.logger.error(msg)
            result["error"] = msg
            return result, 500
        embedd_helper = ToolEmbedding(tool=util)
        embedding_li = embedd_helper.get_embedding(
            adapter_instance_id=output[PSKeys.EMBEDDING]
        )
        if embedding_li is None:
            msg = f"Couldn't fetch embedding {output[PSKeys.EMBEDDING]}"
            app.logger.error(msg)
            result["error"] = msg
            return result, 500
        embedding_dimension = embedd_helper.get_embedding_length(embedding_li)

        service_context = UNServiceContext.get_service_context(
            platform_api_key=platform_key, llm=llm_li, embed_model=embedding_li
        )
        vdb_helper = ToolVectorDB(
            tool=util,
        )
        vector_db_li = vdb_helper.get_vector_db(
            adapter_instance_id=output[PSKeys.VECTOR_DB],
            embedding_dimension=embedding_dimension,
        )
        if vector_db_li is None:
            msg = f"Couldn't fetch vector DB {output[PSKeys.VECTOR_DB]}"
            app.logger.error(msg)
            result["error"] = msg
            return result, 500
        vector_index = VectorStoreIndex.from_vector_store(
            vector_store=vector_db_li, service_context=service_context
        )

        context = ""
        if output[PSKeys.CHUNK_SIZE] == 0:
            # We can do this only for chunkless indexes
            context = tool_index.get_text_from_index(
                embedding_type=output[PSKeys.EMBEDDING],
                vector_db=output[PSKeys.VECTOR_DB],
                doc_id=doc_id,
            )

        assertion_failed = False
        answer = "yes"

        is_assert = output[PSKeys.IS_ASSERT]
        if is_assert:
            app.logger.info(f'Asserting prompt: {output["assert_prompt"]}')
            answer = construct_and_run_prompt(
                output,
                llm_helper,
                llm_li,
                context,
                "assert_prompt",
            )
            app.logger.info(f"Assert response: {answer}")
        if answer.startswith("No") or answer.startswith("no"):
            app.logger.info("Assert failed.")
            assertion_failed = True
            answer = ""
            if (
                output[PSKeys.ASSERTION_FAILURE_PROMPT]
                .lower()
                .startswith("@assign")
            ):
                answer = "NA"
                first_space_index = output[
                    PSKeys.ASSERTION_FAILURE_PROMPT
                ].find(" ")
                if first_space_index > 0:
                    answer = output[PSKeys.ASSERTION_FAILURE_PROMPT][
                        first_space_index + 1 :  # noqa
                    ]
                app.logger.info(f"[Assigning] {answer} to the output")
            else:
                answer = construct_and_run_prompt(
                    output,
                    llm_helper,
                    llm_li,
                    context,
                    "assertion_failure_prompt",
                )
        else:
            if chunk_size == 0:
                answer = construct_and_run_prompt(
                    output,
                    llm_helper,
                    llm_li,
                    context,
                    "promptx",
                )
            else:
                answer = "NA"
                if output[PSKeys.RETRIEVAL_STRATEGY] == PSKeys.SIMPLE:
                    answer, context = simple_retriver(
                        output,
                        doc_id,
                        llm_helper,
                        llm_li,
                        vector_index,
                    )

                    # query_engine = vector_index.as_query_engine(
                    #     filters=MetadataFilters(
                    #         filters=[ExactMatchFilter(key="doc_id", value=doc_id)],  # noqa
                    #     ),
                    #     similarity_top_k=output['similarity-top-k'],
                    # )
                    # r = query_engine.query(output['promptx'])
                    # print(r)
                    # answer = r.response

                elif output[PSKeys.RETRIEVAL_STRATEGY] == PSKeys.VECTOR_KEYWORD:
                    # TODO: Currently the retriever is restricted to keywords only.  # noqa
                    # TODO: We need to add the vector retriever as well (removed due to context length)  # noqa
                    answer, context = vector_keyword_retriver(
                        output,
                        util,
                        doc_id,
                        service_context,
                        vector_db_li,
                        vector_index,
                    )
                elif output[PSKeys.RETRIEVAL_STRATEGY] == PSKeys.SUBQUESTION:
                    answer, context = subquestion_retriver(
                        output, doc_id, service_context, vector_index
                    )
                    # nodes = response.source_nodes
                    # print(nodes)
                else:
                    app.logger.info("No retrieval strategy matched")

        if output[PSKeys.TYPE] == PSKeys.NUMBER:
            if assertion_failed or answer.lower() == "na":
                structured_output[output[PSKeys.NAME]] = None
            else:
                # TODO: Extract these prompts as constants after pkging
                prompt = f"Extract the number from the following \
                    text:\n{answer}\n\nOutput just the number. \
                    If the number is expressed in millions \
                    or thousands, expand the number to its numeric value \
                    The number should be directly assignable\
                    to a numeric variable.\
                    It should not have any commas, \
                    percentages or other grouping \
                    characters. No explanation is required.\
                    If you cannot extract the number, output 0."
                answer = run_completion(
                    llm_helper,
                    llm_li,
                    prompt,
                )
                try:
                    structured_output[output[PSKeys.NAME]] = float(answer)
                except Exception as e:
                    app.logger.info(
                        f"Error parsing response (to numeric, float): {e}",
                        LogLevel.ERROR,
                    )
                    structured_output[output[PSKeys.NAME]] = None
        elif output[PSKeys.TYPE] == PSKeys.EMAIL:
            if assertion_failed or answer.lower() == "na":
                structured_output[output[PSKeys.NAME]] = None
            else:
                prompt = f'Extract the email from the following text:\n{answer}\n\nOutput just the email. \
                    The email should be directly assignable to a string variable. \
                        No explanation is required. If you cannot extract the email, output "NA".'  # noqa
                answer = run_completion(
                    llm_helper,
                    llm_li,
                    prompt,
                )
                structured_output[output[PSKeys.NAME]] = answer
        elif output[PSKeys.TYPE] == PSKeys.DATE:
            if assertion_failed or answer.lower() == "na":
                structured_output[output[PSKeys.NAME]] = None
            else:
                prompt = f'Extract the date from the following text:\n{answer}\n\nOutput just the date.\
                      The date should be in ISO date time format. No explanation is required. \
                        The date should be directly assignable to a date variable. \
                            If you cannot convert the string into a date, output "NA".'  # noqa
                answer = run_completion(
                    llm_helper,
                    llm_li,
                    prompt,
                )
                structured_output[output[PSKeys.NAME]] = answer

        elif output[PSKeys.TYPE] == PSKeys.BOOLEAN:
            if assertion_failed or answer.lower() == "na":
                structured_output[output[PSKeys.NAME]] = None
            else:
                if answer.lower() == "yes":
                    structured_output[output[PSKeys.NAME]] = True
                else:
                    structured_output[output[PSKeys.NAME]] = False
        elif output[PSKeys.TYPE] == PSKeys.JSON:
            if (
                assertion_failed
                or answer.lower() == "[]"
                or answer.lower() == "na"
            ):
                structured_output[output[PSKeys.NAME]] = None
            else:
                # Remove any markdown code blocks
                lines = answer.split("\n")
                answer = ""
                for line in lines:
                    if line.strip().startswith("```"):
                        continue
                    answer += line + "\n"
                try:
                    structured_output[output[PSKeys.NAME]] = json.loads(answer)
                except Exception as e:
                    app.logger.info(
                        f"JSON format error : {answer}", LogLevel.ERROR
                    )
                    app.logger.info(
                        f"Error parsing response (to json): {e}", LogLevel.ERROR
                    )
                    structured_output[output[PSKeys.NAME]] = []
        else:
            structured_output[output[PSKeys.NAME]] = answer

        # If there is a trailing '\n' remove it
        if isinstance(structured_output[output[PSKeys.NAME]], str):
            structured_output[output[PSKeys.NAME]] = structured_output[
                output[PSKeys.NAME]
            ].rstrip("\n")

        #
        # Evaluate the prompt.
        #
        if (
            PSKeys.EVAL_SETTINGS in output
            and output[PSKeys.EVAL_SETTINGS][PSKeys.EVAL_SETTINGS_EVALUATE]
        ):
            eval_plugin: dict[str, Any] = plugins.get("evaluation", {})
            try:
                if eval_plugin:
                    evaluator = eval_plugin["entrypoint_cls"](
                        "",
                        context,
                        "",
                        "",
                        output,
                        structured_output,
                        app.logger,
                        platform_key,
                    )
                    # Will inline replace the structured output passed.
                    evaluator.run()
                else:
                    app.logger.info(
                        f'No eval plugin found to evaluate prompt: {output["name"]}'  # noqa: E501
                    )
            except eval_plugin["exception_cls"] as e:
                app.logger.error(
                    f'Failed to evaluate prompt {output["name"]}: {str(e)}'
                )
        #
        #
        #

    for k, v in structured_output.items():
        if isinstance(v, str) and v.lower() == "na":
            structured_output[k] = None
        elif isinstance(v, list):
            for i in range(len(v)):
                if isinstance(v[i], str) and v[i].lower() == "na":
                    v[i] = None
                elif isinstance(v[i], dict):
                    for k1, v1 in v[i].items():
                        if isinstance(v1, str) and v1.lower() == "na":
                            v[i][k1] = None
        elif isinstance(v, dict):
            for k1, v1 in v.items():
                if isinstance(v1, str) and v1.lower() == "na":
                    v[k1] = None

    return structured_output


def subquestion_retriver(
    output: dict[str, Any],
    doc_id: str,
    service_context: ServiceContext,
    vector_index: VectorStoreIndex,
) -> tuple[Any, str]:
    query_engine = vector_index.as_query_engine(
        filters=MetadataFilters(
            filters=[ExactMatchFilter(key=PSKeys.DOC_ID, value=doc_id)],
        ),
        similarity_top_k=output[PSKeys.SIMILARITY_TOP_K],
    )
    query_engine_tools = [
        QueryEngineTool(
            query_engine=query_engine,
            metadata=ToolMetadata(
                name="unstract-subquestion",
                description="Subquestion query engine",
            ),
        ),
    ]
    query_engine = SubQuestionQueryEngine.from_defaults(
        query_engine_tools=query_engine_tools,
        service_context=service_context,
        use_async=True,
    )

    prompt = f"{output[PSKeys.PREAMBLE]}\n\n{output[PSKeys.PROMPTX]}"
    response = query_engine.query(prompt)
    answer = response.response  # type:ignore
    # Retrieves all the source nodes contents truncated to input length.
    sources_text = response.get_formatted_sources(10000)
    return (answer, sources_text)


def vector_keyword_retriver(
    output: dict[str, Any],
    util: BaseTool,
    doc_id: str,
    service_context: ServiceContext,
    vector_db_li: VectorStore,
    vector_index: VectorStoreIndex,
) -> tuple[Any, str]:
    retriever = UnstractRetriever_V_K(
        vector_index,
        doc_id,
        vector_db_li,
        "unstract_vector_db",
        service_context,
        util,
    )
    response_synthesizer = get_response_synthesizer(
        service_context=service_context,
        verbose=True,
    )
    custom_query_engine = RetrieverQueryEngine(
        retriever=retriever,
        response_synthesizer=response_synthesizer,
    )
    response = custom_query_engine.query(output[PSKeys.PROMPTX])
    answer = response.response  # type:ignore
    # Retrieves all the source nodes contents truncated to input length.
    sources_text = response.get_formatted_sources(10000)
    return (answer, sources_text)


def simple_retriver(  # type:ignore
    output: dict[str, Any],
    doc_id: str,
    llm_helper: ToolLLM,
    llm_li: Optional[LLM],
    vector_index,
) -> tuple[str, str]:
    prompt = construct_prompt_for_engine(
        preamble=output["preamble"],
        prompt=output["promptx"],
        postamble=output["postamble"],
        grammar_list=output["grammar"],
    )
    subq_prompt = (
        f"Generate a sub-question from the following verbose prompt that will"
        f" help extract relevant documents from a vector store:\n\n{prompt}"
    )
    answer: str = run_completion(
        llm_helper,
        llm_li,
        subq_prompt,
    )

    retriever = vector_index.as_retriever(
        similarity_top_k=output[PSKeys.SIMILARITY_TOP_K],
        filters=MetadataFilters(
            filters=[
                ExactMatchFilter(key="doc_id", value=doc_id),
                # TODO: Enable after adding section in GUI
                # ExactMatchFilter(
                #     key="section", value=output["section"]
            ],
        ),
    )
    nodes = retriever.retrieve(answer)
    text = ""
    for node in nodes:
        if node.score > 0.6:
            text += node.get_content() + "\n"
        else:
            app.logger.info(
                "Node score is less than 0.6. " f"Ignored: {node.score}"
            )

    answer: str = construct_and_run_prompt(  # type:ignore
        output,
        llm_helper,
        llm_li,
        text,
        "promptx",
    )
    return (answer, text)


def construct_and_run_prompt(
    output: dict[str, Any],
    llm_helper: ToolLLM,
    llm_li: Optional[LLM],
    context: str,
    prompt: str,
) -> str:
    prompt = construct_prompt(
        preamble=output[PSKeys.PREAMBLE],
        prompt=output[prompt],
        postamble=output[PSKeys.POSTAMBLE],
        grammar_list=output[PSKeys.GRAMMAR],
        context=context,
    )
    try:
        answer: str = run_completion(
            llm_helper,
            llm_li,
            prompt,
        )
        return answer
    except Exception as e:
        app.logger.info(f"Error completing prompt: {e}.")
        raise e


def run_completion(
    llm_helper: ToolLLM,
    llm_li: Optional[LLM],
    prompt: str,
) -> str:
    try:
        platform_api_key = llm_helper.tool.get_env_or_die(
            PSKeys.PLATFORM_SERVICE_API_KEY
        )
        completion = llm_helper.run_completion(
            llm_li, platform_api_key, prompt, 3
        )

        answer: str = completion[PSKeys.RESPONSE].text
        return answer
    except Exception as e:
        app.logger.info(f"Error completing prompt: {e}.")
        raise e


def extract_variable(
    structured_output: dict[str, Any],
    variable_names: list[Any],
    output: dict[str, Any],
    promptx: str,
) -> str:
    for variable_name in variable_names:
        if promptx.find(f"%{variable_name}%") >= 0:
            if variable_name in structured_output:
                promptx = promptx.replace(
                    f"%{variable_name}%",
                    str(structured_output[variable_name]),
                )
            else:
                raise ValueError(
                    f"Variable {variable_name} not found "
                    "in structured output"
                )

    if promptx != output[PSKeys.PROMPT]:
        app.logger.info(f"Prompt after variable replacement: {promptx}")
    return promptx
    # app.logger.info(f"Total Tokens: {total_extraction_tokens}")
    # with open(f"/tmp/json_of_{file_name_without_path}.json", "w") as f:
    #     f.write(json.dumps(structured_output, indent=2))


if __name__ == "__main__":
    # Start the server
    app.run(host="0.0.0.0", port=5003)