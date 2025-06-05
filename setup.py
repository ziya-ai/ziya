from setuptools import setup, find_packages

setup(
    name="ziya",
    version="0.2.3",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "app": ["templates/**/*", "utils/ast_parser/ts_parser/**/*"],
        "": ["templates/**/*"],  # Include templates at root level
    },
)
