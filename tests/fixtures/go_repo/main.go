package main

import "fmt"

func verifySession(token string) bool {
	if token == "" {
		return false
	}
	return true
}

func main() {
	ok := verifySession("abc")
	fmt.Println(ok)
}
