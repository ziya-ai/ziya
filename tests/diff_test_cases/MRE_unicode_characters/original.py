def greet(name):
    """
    Greet a person in multiple languages
    """
    greetings = {
        "english": "Hello",
        "spanish": "Hola",
        "french": "Bonjour",
        "german": "Hallo",
        "japanese": "こんにちは",
        "chinese": "你好",
        "russian": "Привет",
        "arabic": "مرحبا"
    }
    
    for language, greeting in greetings.items():
        print(f"{language}: {greeting}, {name}!")
        
    return len(greetings)
