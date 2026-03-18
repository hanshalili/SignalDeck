from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = [
        line.strip()
        for line in f
        if line.strip() and not line.startswith("#")
    ]

setup(
    name="signaldeck-ai",
    version="1.0.0",
    description="SignalDeck AI: Intelligent Market Analysis System",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=install_requires,
    entry_points={
        "console_scripts": [
            "signaldeck=run_pipeline:main",
        ],
    },
)
