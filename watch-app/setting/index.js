AppSettingsPage({
  build(props) {
    const storage = props.settingsStorage
    const serverUrl = storage.getItem('serverUrl') || ''
    const user = storage.getItem('user') || 'admin'
    const password = storage.getItem('password') || ''

    return Section(
      {
        style: { padding: '12px' },
      },
      [
        Text(
          {
            bold: true,
            style: { fontSize: '18px', display: 'block', marginBottom: '8px' },
          },
          'Monitor de IA'
        ),
        Text(
          {
            style: {
              fontSize: '13px',
              color: '#666',
              display: 'block',
              marginBottom: '12px',
            },
          },
          'Endereco do painel Monitor de IA. Ex: https://meupainel.com ou http://192.168.0.10:8765 (celular na mesma rede Wi-Fi).'
        ),
        TextInput({
          label: 'Endereco do painel',
          placeholder: 'https://seu-servidor',
          value: serverUrl,
          onChange: (v) => storage.setItem('serverUrl', String(v).trim()),
        }),
        TextInput({
          label: 'Usuario (padrao: admin)',
          placeholder: 'admin',
          value: user,
          onChange: (v) => storage.setItem('user', String(v).trim()),
        }),
        TextInput({
          label: 'Senha (deixe vazio se o painel nao tem senha)',
          placeholder: 'senha do painel',
          value: password,
          onChange: (v) => storage.setItem('password', String(v)),
        }),
      ]
    )
  },
})
