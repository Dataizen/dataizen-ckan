from setuptools import setup, find_packages

setup(
    name='ckanext-rag',
    version='0.1',
    license='AGPL-3.0',
    description='Fraicheur du RAG Dataizen : reindexe le service dtz-rag au depot/maj des jeux',
    packages=find_packages(),
    namespace_packages=['ckanext'],
    entry_points='''
        [ckan.plugins]
        rag=ckanext.rag.plugin:RagPlugin
    ''',
)
