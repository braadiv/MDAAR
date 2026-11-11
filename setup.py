import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="mdaar",
    version="1.0.0",
    author="MDAAR Community",
    author_email="mdaar@example.com",
    description="Multi-role Dynamic Architecture for Automated Repositories (AI-Driven Software Documentation Framework)",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/braadiv/MDAAR",
    py_modules=["mdaar_pipeline"],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Documentation",
    ],
    python_requires=">=3.8",
    install_requires=[
        "mkdocs-material>=9.5.0",
        "pymdown-extensions>=10.7",
        "google-genai>=2.0.0",
    ],
)
