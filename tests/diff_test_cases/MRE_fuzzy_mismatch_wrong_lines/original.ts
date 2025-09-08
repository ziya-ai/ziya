                    if (errorResponse) {
                        console.log("Current content when error detected:", currentContent.substring(0, 200) + "...");
                        console.log("Current content length:", currentContent.length);
                        console.log("Error detected in SSE data:", errorResponse);

                        // Check if the error data contains preserved content and dispatch it
                        try {
