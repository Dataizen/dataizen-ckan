from setuptools import setup, find_packages

setup(
    name="pygeoapi-ckan-provider",
    version="0.1.0",
    description="CKAN Provider for pygeoapi",
    packages=find_packages(),
    install_requires=[
        "pygeoapi",
        "requests",
    ],
    entry_points={
        "pygeoapi.providers": [
            "ckan=ckan_provider.provider:CKANProvider"
        ]
    },
    python_requires=">=3.8",
)


