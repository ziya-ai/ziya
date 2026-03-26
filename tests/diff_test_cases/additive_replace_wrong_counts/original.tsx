const r = () => {
    return tokens.map((t, index) => {
        switch (det) {
            case "tool":
                if (isSecErr) {
                    return (
                        <Alert key={index} msg="blocked" />
                    );
                }

                if (isThink) {
                    return (
                        <Think key={index} dark={d}>
                            {content}
                        </Think>
                    );
                }
                return <Tool key={index} name={n} />;
        }
    });
};
