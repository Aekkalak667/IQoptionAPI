from setuptools import setup, find_packages

setup(
    name="iq-option-nexus-api",
    version="1.2.0",
    author="Aekkalak",
    author_email="aekkalak@example.com",
    description="A high-performance IQ Option API wrapper.",
    long_description="A high-performance IQ Option API wrapper for binary and digital options.",
    long_description_content_type="text/markdown",
    url="https://github.com/Aekkalak/iq-option-nexus-api",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "websockets",
        "requests",
        "python-dotenv",
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.7",
)
