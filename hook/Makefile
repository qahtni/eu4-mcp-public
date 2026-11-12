eu4hook.so: eu4hook.cpp
	g++ -std=c++17 -O2 -g -fPIC -shared -Wall -Wextra -fno-gnu-unique -o $@ $< -ldl -lpthread
clean:
	rm -f eu4hook.so
