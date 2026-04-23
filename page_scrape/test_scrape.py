from main import fetch_url_content


def run_test():
    # Wikipedia is a great test target because it has reliable tables and images
    test_url = "https://en.wikipedia.org/wiki/Web_scraping"

    print(f"Fetching content from: {test_url}")
    print("This may take a few seconds...\n")

    try:
        # Calling the tool directly with default arguments (tables and images included)
        result = fetch_url_content(test_url)

        print("========== EXTRACTION RESULT ==========\n")
        print(result)
        print("\n=======================================")
        print("Test completed successfully!")

    except Exception as e:
        print(f"Test failed with error: {e}")


if __name__ == "__main__":
    run_test()
