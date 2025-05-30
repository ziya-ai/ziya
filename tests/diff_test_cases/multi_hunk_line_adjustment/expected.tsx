export const TestComponent: React.FC = () => {
    return (
        <div>
            <Card style={{ flex: 1 }}>
                <Tabs style={{ height: '100%' }}>
                    <TabPane
                        tab="Tab 1"
                        key="tab1"
                        style={{ flex: 1, overflow: 'auto' }}
                    >
                        <div>Content 1</div>
                    </TabPane>
                    <TabPane
                        tab="Tab 2"
                        key="tab2"
                        style={{ height: 'calc(100vh - 220px)', overflow: 'auto' }}
                    >
                        <div>Content 2</div>
                    </TabPane>
                    <TabPane
                        tab="Tab 3"
                        key="tab3"
                        style={{ height: 'calc(100vh - 220px)', overflow: 'auto' }}
                    >
                        <div>Content 3</div>
                    </TabPane>
                    <TabPane
                        tab="Tab 4"
                        key="tab4"
                        style={{ height: 'calc(100vh - 220px)', overflow: 'auto' }}
                    >
                        <div>Content 4</div>
                    </TabPane>
                    <TabPane
                        tab="Tab 5"
                        key="tab5"
                        style={{ height: 'calc(100vh - 220px)', overflow: 'auto' }}
                    >
                        <div>Content 5</div>
                    </TabPane>
                </Tabs>
            </Card>
        </div>
    );
};
