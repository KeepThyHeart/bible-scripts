# Makefile for sword2bible converter
#
# Builds a standalone C++ program that converts SWORD Bible modules
# to SQLite database format.
#
# Dependencies:
#   - libsword-dev
#   - libsqlite3-dev
#   - libzip-dev
#   - g++ with C++17 support

CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O3 -I/usr/include/sword -I../common
LDFLAGS = -L../common -lswordcommon -lsword -lsqlite3 -lzip -lz -l:libzstd.so.1

TARGET = sword2bible
SOURCE = sword2bible.cpp
COMMON_LIB = ../common/libswordcommon.a

# Build the converter
all: $(TARGET)

$(COMMON_LIB):
	$(MAKE) -C ../common

$(TARGET): $(SOURCE) $(COMMON_LIB)
	@echo "Building $(TARGET)..."
	$(CXX) $(CXXFLAGS) -o $(TARGET) $(SOURCE) $(LDFLAGS)
	@echo "Build complete: ./$(TARGET)"

# Clean build artifacts
clean:
	@echo "Cleaning..."
	rm -f $(TARGET)
	@echo "Clean complete"

# Install system dependencies (Ubuntu/Debian)
install-deps:
	@echo "Installing dependencies (requires sudo)..."
	sudo apt-get update
	sudo apt-get install -y \
		build-essential \
		libsword-dev \
		libsqlite3-dev \
		libzip-dev \
		zlib1g-dev
	@echo "Dependencies installed"

# Test with KJV.zip
test: $(TARGET)
	@echo "Testing conversion with KJV.zip..."
	./$(TARGET) --input KJV.zip --output bible_kjv.db
	@echo "Test complete. Output: bible_kjv.db"

# Inspect the resulting database
inspect: bible_kjv.db
	@echo "Module identity:"
	@sqlite3 bible_kjv.db "SELECT module_uuid, format, format_version, versification, license_spdx, content_sha256 FROM module_info;"
	@echo ""
	@echo "Verse count:"
	@sqlite3 bible_kjv.db "SELECT COUNT(*) FROM bible_verse;"
	@echo ""
	@echo "Sample verses (Genesis 1:1-3) -- text is clean canonical UTF-8:"
	@sqlite3 bible_kjv.db "SELECT verse_id, text FROM bible_verse WHERE verse_id BETWEEN 1001001 AND 1001003;"
	@echo ""
	@echo "Sample formatting spans (Psalm 23:1):"
	@sqlite3 bible_kjv.db "SELECT verse_id, text, formatting FROM bible_verse WHERE verse_id = 19023001;"

# Show help
help:
	@echo "sword2bible Makefile"
	@echo ""
	@echo "Targets:"
	@echo "  make              - Build the converter"
	@echo "  make clean        - Remove build artifacts"
	@echo "  make install-deps - Install system dependencies"
	@echo "  make test         - Build and test with KJV.zip"
	@echo "  make inspect      - Inspect the resulting database"
	@echo "  make help         - Show this help"

.PHONY: all clean install-deps test inspect help
